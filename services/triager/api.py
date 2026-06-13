"""FastAPI surface for the triager-api Modal app.

Routes: health probes, manual classify replay (/classify-email), and the
Gmail Pub/Sub push receiver (/gmail-webhook).
Design notes: docs/design.md.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import UTC, datetime, timedelta
from functools import lru_cache, partial
from typing import Any, cast

from anyio import to_thread
from fastapi import Depends, FastAPI, HTTPException, Request, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from googleapiclient.errors import HttpError
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from services.triager.runner import process_message
from shared import __version__
from shared.auth import verify_bearer
from shared.db import GmailWatchState, get_session
from shared.gmail import build_gmail_service
from shared.health import build_health_router
from shared.settings import get_settings

logger = logging.getLogger(__name__)

api = FastAPI(title="triager-api", version=__version__)
api.include_router(build_health_router())


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ClassifyEmailRequest(BaseModel):
    message_id: str


class ClassifyEmailResponse(BaseModel):
    primary_label: str
    flagged: bool
    reasoning: str


# ---------------------------------------------------------------------------
# Manual classifier replay
# ---------------------------------------------------------------------------


@api.post(
    "/classify-email",
    response_model=ClassifyEmailResponse,
    dependencies=[Depends(verify_bearer)],
)
async def classify_email(request: ClassifyEmailRequest) -> ClassifyEmailResponse:
    """Classify a single Gmail message and apply the resulting label.

    Delegates to runner.process_message and only maps exceptions to HTTP
    statuses: 404 when Gmail reports the message id missing, 503 for any
    other failure with the exception class name in detail.
    """
    try:
        classification = await process_message(request.message_id)
    except HttpError as exc:
        if exc.resp.status == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"gmail message not found: {request.message_id}",
            ) from exc
        logger.exception("classify_email.failed", extra={"message_id": request.message_id})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"classify-email failed: {type(exc).__name__}",
        ) from exc
    except Exception as exc:  # Any other failure (classify, apply, DB) maps to 503.
        logger.exception("classify_email.failed", extra={"message_id": request.message_id})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"classify-email failed: {type(exc).__name__}",
        ) from exc

    return ClassifyEmailResponse(
        primary_label=classification.primary_label,
        flagged=classification.flagged,
        reasoning=classification.reasoning,
    )


# ---------------------------------------------------------------------------
# Pub/Sub push receiver
# ---------------------------------------------------------------------------


def _extract_bearer_token(authorization: str | None) -> str:
    """Pull the bearer token out of an Authorization header.

    Pub/Sub push always sends Authorization: Bearer <jwt>. A missing
    header, wrong scheme, or empty token raises 401.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Authorization header",
        )
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid Authorization header",
        )
    return parts[1].strip()


@lru_cache(maxsize=1)
def _cert_transport() -> google_requests.Request:
    """Return a cached transport for fetching Google's signing certs.

    One transport keeps the pooled HTTPS connection alive across webhook
    deliveries. The session is shared across anyio worker threads;
    accepted low-risk since deliveries are near-serial at this scale and
    verification only GETs certs.
    """
    return google_requests.Request()


def _verify_pubsub_jwt(token: str) -> dict[str, Any]:
    """Validate the JWT against our audience and service-account claim.

    Raises HTTPException 401 on any failure. Blocking: fetches Google's
    signing certs over HTTP, so async callers must offload to a worker
    thread.
    """
    settings = get_settings()
    try:
        claims = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
            token,
            _cert_transport(),
            audience=settings.gmail_pubsub_audience,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid Pub/Sub JWT: {exc}",
        ) from exc
    if claims.get("email") != settings.gmail_pubsub_push_sa:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Pub/Sub JWT not signed by expected service account",
        )
    return cast("dict[str, Any]", claims)


def _decode_pubsub_envelope(envelope: dict[str, Any]) -> tuple[str, int]:
    """Extract (emailAddress, historyId) from a Pub/Sub push envelope.

    The Gmail notification is base64 JSON under message.data; historyId
    arrives as both int and string in the wild, so coerce to int.
    """
    message = envelope.get("message") or {}
    data = message.get("data")
    if not isinstance(data, str) or not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pub/Sub envelope missing message.data",
        )
    try:
        decoded = base64.b64decode(data).decode("utf-8")
        payload = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pub/Sub message.data decode failed: {type(exc).__name__}",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pub/Sub data did not decode to an object",
        )
    email = payload.get("emailAddress")
    history_id = payload.get("historyId")
    if not isinstance(email, str) or history_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pub/Sub data missing emailAddress or historyId",
        )
    try:
        return email, int(history_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pub/Sub historyId not coercible to int: {history_id!r}",
        ) from exc


def _collect_added_message_ids(*, service: Any, start_history_id: int) -> list[str]:
    """Page through users.history.list and return new messageAdded ids in order.

    Filters to messageAdded records with labelId="INBOX" to match the
    INBOX-scoped watch. Without the label filter, history.list returns
    every mailbox change since the cursor, including sent mail, which
    would get fetched and classified (billed) on the next inbound
    notification. De-dupes: one message can appear in multiple history
    records if labels also changed in the same window.
    """
    seen: set[str] = set()
    ordered_ids: list[str] = []
    page_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": str(start_history_id),
            "historyTypes": ["messageAdded"],
            "labelId": "INBOX",
        }
        if page_token:
            kwargs["pageToken"] = page_token
        response: dict[str, Any] = service.users().history().list(**kwargs).execute()
        for record in response.get("history") or []:
            for added in record.get("messagesAdded") or []:
                msg = added.get("message") or {}
                msg_id = msg.get("id")
                if isinstance(msg_id, str) and msg_id not in seen:
                    seen.add(msg_id)
                    ordered_ids.append(msg_id)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return ordered_ids


async def _advance_cursor(*, email: str, history_id: int) -> None:
    """Atomically advance the stored cursor to at least history_id.

    Pub/Sub does not guarantee ordering: a late-arriving older
    notification must never rewind the cursor, or the next webhook
    re-fetches (and re-bills) the whole window behind it. GREATEST inside
    a single UPDATE keeps the advance atomic; a Python-side max() over a
    separately read value could still commit a rewind when two webhooks
    race.
    """
    async with get_session() as session:
        await session.execute(
            update(GmailWatchState)
            .where(GmailWatchState.email == email)
            .values(
                current_history_id=func.greatest(GmailWatchState.current_history_id, history_id)
            )
        )
        await session.commit()


@api.post("/gmail-webhook")
async def gmail_webhook(request: Request) -> dict[str, Any]:
    """Pub/Sub push receiver for Gmail change notifications.

    Verifies the OIDC token, fetches every messageAdded record since the
    stored cursor, classifies each through process_message, and advances
    the cursor. Returns 200 on every path that did its job, including
    per-message classify failures: Pub/Sub retries non-2xx, and one bad
    email must not trigger a redelivery storm. Failed messages land as
    errored triager_runs rows, replayable via /classify-email.
    """
    token = _extract_bearer_token(request.headers.get("authorization"))
    # Worker thread: verification fetches Google's signing certs over
    # blocking HTTP.
    await to_thread.run_sync(partial(_verify_pubsub_jwt, token))

    try:
        envelope = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"webhook body not valid JSON: {exc}",
        ) from exc

    email, notification_history_id = _decode_pubsub_envelope(envelope)

    async with get_session() as session:
        result = await session.execute(
            select(GmailWatchState).where(GmailWatchState.email == email)
        )
        watch_state = result.scalar_one_or_none()

    # First-time webhook: no resume cursor exists yet, so there is
    # nothing to fetch. Initialize the watch row and let the next
    # notification drive the first batch.
    if watch_state is None:
        async with get_session() as session:
            # ON CONFLICT DO NOTHING: two concurrent first-ever webhooks
            # can both reach this INSERT; the loser must not 500 against
            # the route's always-200 contract.
            await session.execute(
                insert(GmailWatchState)
                .values(
                    email=email,
                    current_history_id=notification_history_id,
                    # Provisional value: this route never calls
                    # users.watch(), so the true expiration is unknown
                    # here. The next renewal cron overwrites it with
                    # Gmail's actual value.
                    expires_at=datetime.now(tz=UTC) + timedelta(days=7),
                )
                .on_conflict_do_nothing(index_elements=[GmailWatchState.email])
            )
            await session.commit()
        logger.info(
            "gmail_webhook.initialized",
            extra={"email": email, "history_id": notification_history_id},
        )
        return {"status": "initialized", "processed": 0}

    start_history_id = watch_state.current_history_id
    try:
        gmail_service = build_gmail_service()
        # Worker thread: history.list paging is blocking Gmail HTTP.
        message_ids = await to_thread.run_sync(
            partial(
                _collect_added_message_ids,
                service=gmail_service,
                start_history_id=start_history_id,
            )
        )
    except HttpError as exc:
        if exc.resp.status != 404:
            logger.exception("gmail_webhook.history_list_failed", extra={"email": email})
            # Return 200: Pub/Sub redelivery would only cause API thrash.
            # Cursor stays put; the next notification retries this window.
            return {"status": "history_list_failed", "processed": 0}
        # A 404 means startHistoryId fell outside Gmail's history retention
        # window (typically ~1 week; the API mandates a full sync).
        # Retrying the same cursor would 404 forever, so re-seed it from
        # the notification's historyId (Gmail's current cursor for this
        # mailbox) instead of full-syncing, which would re-classify and
        # re-bill the whole INBOX. Mail from inside the expired window is
        # skipped; the gap is bounded by the retention window and
        # replayable via /classify-email.
        await _advance_cursor(email=email, history_id=notification_history_id)
        logger.warning(
            "gmail_webhook.history_cursor_reset",
            extra={
                "email": email,
                "stale_history_id": start_history_id,
                "new_history_id": notification_history_id,
                "reason": "startHistoryId exceeded Gmail history retention; bounded gap skipped",
            },
        )
        return {"status": "history_cursor_reset", "processed": 0}
    except Exception:  # Transient (network, auth); same contract as above.
        logger.exception("gmail_webhook.history_list_failed", extra={"email": email})
        # Return 200: Pub/Sub redelivery would only cause API thrash.
        # Cursor stays put; the next notification retries this window.
        return {"status": "history_list_failed", "processed": 0}

    processed = 0
    failed = 0
    for msg_id in message_ids:
        try:
            await process_message(msg_id, service=gmail_service)
            processed += 1
        except Exception:  # One bad email must not fail the batch.
            failed += 1
            logger.exception(
                "gmail_webhook.process_failed",
                extra={"email": email, "message_id": msg_id},
            )

    await _advance_cursor(email=email, history_id=notification_history_id)

    return {
        "status": "ok",
        "processed": processed,
        "failed": failed,
        "found": len(message_ids),
    }
