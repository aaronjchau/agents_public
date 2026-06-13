"""FastAPI surface for the job-apps-api Modal app.

Routes: health probes, manual replay (/run-job-apps), the Triager's
cross-service dispatch (/internal/dispatch), and the drafts dashboard
view, the only dashboard view served here.
"""

from __future__ import annotations

import logging
import traceback
from datetime import UTC, datetime
from typing import Annotated, Any

from anyio import to_thread
from fastapi import Depends, FastAPI, HTTPException, Response, status
from googleapiclient.errors import HttpError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.job_apps.dashboard_models import (
    JobAppsDraftRow,
    JobAppsDraftsResponse,
)
from services.job_apps.dashboard_queries import fetch_runs_by_message_ids
from services.job_apps.runner import process_job_apps_message
from shared import __version__
from shared.auth import verify_bearer
from shared.db import session_dependency
from shared.gmail import build_gmail_service, parse_message_received_at
from shared.health import build_health_router

logger = logging.getLogger(__name__)

api = FastAPI(title="job-apps-api", version=__version__)
api.include_router(build_health_router())


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class RunJobAppsRequest(BaseModel):
    message_id: str


class InternalDispatchRequest(BaseModel):
    message_id: str


class RunJobAppsResponse(BaseModel):
    """Subset of JobAppsState exposed over HTTP.

    Excludes parsed_email, classify_reasoning, and the raw result
    objects; the dashboard renders the flattened scalar fields directly.
    """

    message_id: str
    sublabel: str | None
    match_status: str | None
    notion_row_id: str | None
    status_changed: bool
    new_status: str | None
    terminal_reason: str | None
    errors: list[str]


# ---------------------------------------------------------------------------
# Manual pipeline replay
# ---------------------------------------------------------------------------


@api.post(
    "/run-job-apps",
    response_model=RunJobAppsResponse,
    dependencies=[Depends(verify_bearer)],
)
async def run_job_apps(request: RunJobAppsRequest) -> RunJobAppsResponse:
    """Manually replay the Job Apps pipeline for a single message.

    Runs with raise_on_failure=True so exceptions map to HTTP statuses:
    a Gmail 404 becomes 404, everything else 503 with the exception
    class in detail. The runner writes the audit row before re-raising.
    """
    try:
        state = await process_job_apps_message(request.message_id, raise_on_failure=True)
    except HttpError as exc:
        if exc.resp.status == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"gmail message not found: {request.message_id}",
            ) from exc
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"run-job-apps failed: {type(exc).__name__}",
        ) from exc
    except Exception as exc:  # Everything else (classifier, match, writer, DB) maps to 503.
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"run-job-apps failed: {type(exc).__name__}",
        ) from exc

    status_changed = False
    new_status: str | None = None
    if state.notion_write_result is not None:
        status_changed = state.notion_write_result.status_changed
        new_status = state.notion_write_result.new_status

    return RunJobAppsResponse(
        message_id=state.message_id,
        sublabel=state.sublabel,
        match_status=state.match_result.status if state.match_result else None,
        notion_row_id=state.notion_row_id,
        status_changed=status_changed,
        new_status=new_status,
        terminal_reason=state.terminal_reason,
        errors=state.errors,
    )


# ---------------------------------------------------------------------------
# Internal cross-service dispatch from the Triager
# ---------------------------------------------------------------------------
#
# Bearer auth reuses AGENTS_API_TOKEN; a separate token would add a
# deploy step for negligible blast-radius improvement at this scale.


@api.post(
    "/internal/dispatch",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_bearer)],
)
async def internal_dispatch(req: InternalDispatchRequest) -> Response:
    """Run the pipeline synchronously for a Triager-dispatched message.

    Returns 204 on success and 503 on failure (the runner already
    persisted an errored audit row); the Triager's client wraps the
    call so the Pub/Sub webhook stays 200 either way.
    """
    try:
        await process_job_apps_message(req.message_id)
    except Exception as exc:
        logger.exception("internal_dispatch.failed", extra={"message_id": req.message_id})
        # Surface only the exception class; the message can echo email
        # subject, sender, or body PII into the HTTP response.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=type(exc).__name__,
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Dashboard read: /job-apps/drafts (joins live Gmail to audit rows)
# ---------------------------------------------------------------------------
#
# The only dashboard view served through this API; the live-Gmail join
# has no Postgres-only equivalent.

DbSession = Annotated[AsyncSession, Depends(session_dependency)]


def _draft_subject_sender(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Pull Subject and From from a Gmail message payload.

    Matching is case-insensitive because Gmail preserves whatever header
    case the sender used; forwarded mail varies in the wild.
    """
    headers = payload.get("headers") or []
    subject: str | None = None
    sender: str | None = None
    for h in headers:
        name = (h.get("name") or "").lower()
        value = h.get("value")
        if name == "subject" and isinstance(value, str):
            subject = value
        elif name == "from" and isinstance(value, str):
            sender = value
    return subject, sender


def _draft_created_at(message: dict[str, Any]) -> datetime | None:
    """Best-effort timestamp for a draft.

    Gmail exposes no createdAt on draft resources; the embedded
    message's internalDate and Date header are the closest signals.
    """
    return parse_message_received_at(message)


def _list_drafts_with_messages() -> dict[str, dict[str, Any]]:
    """List drafts and fetch metadata for each, keyed by draft id.

    Blocking Gmail I/O, called via to_thread.run_sync. Drafts that 404
    between the list and the get were deleted in the same poll window
    and are skipped. Any other error propagates for the 503 mapping; a
    degraded panel beats a silently partial one.
    """
    service = build_gmail_service()
    list_resp: dict[str, Any] = service.users().drafts().list(userId="me", maxResults=100).execute()
    message_by_draft: dict[str, dict[str, Any]] = {}
    for stub in list_resp.get("drafts") or []:
        draft_id = stub.get("id")
        if not isinstance(draft_id, str):
            continue
        try:
            full = (
                service.users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                continue
            raise
        message_by_draft[draft_id] = full.get("message") or {}
    return message_by_draft


@api.get(
    "/api/dashboard/job-apps/drafts",
    response_model=JobAppsDraftsResponse,
    dependencies=[Depends(verify_bearer)],
)
async def dashboard_job_apps_drafts(
    session: DbSession,
) -> JobAppsDraftsResponse:
    """Live Gmail drafts joined to job_apps_runs rows.

    Lists every draft in the mailbox (Gmail does not tag pipeline-created
    drafts), pulls metadata per draft, and attaches the sublabel from
    the matching audit row. Any live Gmail failure surfaces as 503 so
    the dashboard shows a degraded panel; per-draft 404s are skipped.
    """
    try:
        # The drafts list plus one metadata get per draft is serial
        # blocking Gmail HTTP; run it on a worker thread so the event
        # loop keeps serving /health and concurrent requests.
        message_by_draft = await to_thread.run_sync(_list_drafts_with_messages)
    except HttpError as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"gmail drafts fetch failed: {exc.resp.status}",
        ) from exc
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"gmail drafts fetch failed: {type(exc).__name__}",
        ) from exc

    message_ids_to_lookup: list[str] = []
    for message in message_by_draft.values():
        raw_id = message.get("id")
        if isinstance(raw_id, str):
            message_ids_to_lookup.append(raw_id)

    audit_rows = await fetch_runs_by_message_ids(session, message_ids_to_lookup)

    rows: list[JobAppsDraftRow] = []
    for draft_id, message in message_by_draft.items():
        raw_msg_id = message.get("id")
        msg_id = raw_msg_id if isinstance(raw_msg_id, str) else None
        subject, sender = _draft_subject_sender(message.get("payload") or {})
        created = _draft_created_at(message)
        audit = audit_rows.get(msg_id) if msg_id else None
        rows.append(
            JobAppsDraftRow(
                draft_id=draft_id,
                message_id=msg_id,
                subject=subject,
                sender=sender,
                sublabel=audit.sublabel if audit else None,
                created_at=created,
                gmail_deep_link=f"https://mail.google.com/mail/u/0/#drafts/{draft_id}",
            )
        )

    rows.sort(
        key=lambda r: r.created_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return JobAppsDraftsResponse(drafts=rows)
