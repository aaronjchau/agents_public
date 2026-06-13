"""Single-message Triager runner: fetch, classify, apply, record.

process_message() is the unit of work shared by the manual /classify-email
endpoint and the /gmail-webhook receiver; a triager_runs row keyed by
message_id makes the run idempotent. Design notes: docs/design.md.
"""

from __future__ import annotations

import logging
import time
from functools import partial
from typing import TYPE_CHECKING, cast

from anyio import to_thread
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from services.triager.classifier import classify
from services.triager.labels import apply_classification
from services.triager.types import Classification, Label
from shared.anthropic_cost import compute_cost_usd
from shared.db import TriagerRun, get_session
from shared.gmail import (
    build_gmail_service,
    extract_plain_text,
    index_headers,
    parse_internal_date,
)

if TYPE_CHECKING:
    from datetime import datetime

    from googleapiclient.discovery import Resource

logger = logging.getLogger(__name__)


async def process_message(
    message_id: str,
    *,
    service: Resource | None = None,
) -> Classification:
    """Fetch, classify, label-apply, and audit a single Gmail message.

    Idempotent: an existing triager_runs row skips the classifier and
    re-applies the stored label so Gmail and the audit log stay aligned,
    returning a Classification reconstructed from the row. service is
    injectable for tests and for the webhook batch path that shares one
    client across many calls. Errors propagate; the caller maps them to
    its own contract.
    """
    stage_timings: dict[str, int] = {}
    # Pre-declared so the failure audit in the except block can record
    # whatever metadata was parsed before the error.
    sender: str | None = None
    subject: str | None = None
    email_received_at: datetime | None = None

    def _record(stage: str, start: float) -> None:
        stage_timings[stage] = int((time.perf_counter() - start) * 1000)

    try:
        stage_start = time.perf_counter()
        existing = await _load_existing(message_id)
        _record("idempotency_check", stage_start)
        if existing is not None and existing.error is not None:
            # Errored audit row: this attempt is a retry. Delete the failure
            # row so the success INSERT below keeps its duplicate-race
            # IntegrityError semantics, then reprocess from scratch.
            async with get_session() as session:
                await session.execute(
                    delete(TriagerRun).where(
                        TriagerRun.message_id == message_id,
                        TriagerRun.error.is_not(None),
                    )
                )
                await session.commit()
            existing = None
        if existing is not None:
            gmail_service = service or build_gmail_service()
            # Re-apply the stored label even on idempotent runs so Gmail
            # state stays consistent if the user manually changed labels.
            # Worker thread: the label apply is blocking Gmail HTTP.
            await to_thread.run_sync(
                partial(
                    apply_classification,
                    message_id=message_id,
                    primary_label=cast("Label", existing.primary_label),
                    flagged=existing.flagged,
                    service=gmail_service,
                )
            )
            return Classification(
                primary_label=cast("Label", existing.primary_label),
                flagged=existing.flagged,
                reasoning=existing.reasoning or "",
            )

        started = time.perf_counter()
        gmail_service = service or build_gmail_service()
        stage_start = time.perf_counter()
        # Worker threads for the Gmail fetch, the classifier call (can
        # run 10s+ with extended thinking), and the label apply: all are
        # blocking I/O that would otherwise starve the event loop and
        # block /health on this container.
        message = await to_thread.run_sync(
            lambda: (
                gmail_service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        )
        _record("fetch", stage_start)
        stage_start = time.perf_counter()
        email_received_at = parse_internal_date(message.get("internalDate"))
        payload = message.get("payload") or {}
        headers = index_headers(payload)
        sender = headers.get("From", "")
        subject = headers.get("Subject", "")
        body_text = extract_plain_text(payload)
        _record("parse", stage_start)

        stage_start = time.perf_counter()
        result = await to_thread.run_sync(
            partial(classify, sender=sender, subject=subject, body_text=body_text)
        )
        _record("classify", stage_start)
        classification = result.classification
        stage_start = time.perf_counter()
        await to_thread.run_sync(
            partial(
                apply_classification,
                message_id=message_id,
                primary_label=classification.primary_label,
                flagged=classification.flagged,
                service=gmail_service,
            )
        )
        _record("label_apply", stage_start)

        latency_ms = int((time.perf_counter() - started) * 1000)
        cost_usd = compute_cost_usd(result.model, result.usage)
        usage = result.usage
        cache_creation_5m: int | None = None
        cache_creation_1h: int | None = None
        if usage.cache_creation is not None:
            cache_creation_5m = usage.cache_creation.ephemeral_5m_input_tokens
            cache_creation_1h = usage.cache_creation.ephemeral_1h_input_tokens
        else:
            cache_creation_5m = usage.cache_creation_input_tokens

        stage_start = time.perf_counter()
        try:
            async with get_session() as session:
                session.add(
                    TriagerRun(
                        message_id=message_id,
                        email_received_at=email_received_at,
                        primary_label=classification.primary_label,
                        flagged=classification.flagged,
                        subject=subject or None,
                        sender=sender or None,
                        reasoning=classification.reasoning,
                        latency_ms=latency_ms,
                        model=result.model,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cache_read_tokens=usage.cache_read_input_tokens,
                        cache_creation_5m=cache_creation_5m,
                        cache_creation_1h=cache_creation_1h,
                        cost_usd=cost_usd,
                        stage_timings_ms=stage_timings,
                    )
                )
                await session.commit()
        except IntegrityError:
            # Concurrent-duplicate race. Gmail emits several push
            # notifications per message and Modal runs webhook invocations
            # in parallel, so two workers can both pass the _load_existing
            # check (separate sessions) and then both INSERT this
            # message_id (the PK). One commit wins; the loser lands here.
            # The winner already applied the label and owns the Job Apps
            # dispatch, so log and return without dispatching. Not
            # re-raised: raising here would crash process_message after the
            # label apply but before dispatch and bury real errors under
            # duplicate-key tracebacks.
            _record("db_write", stage_start)
            logger.info(
                "triager.duplicate_skipped",
                extra={
                    "message_id": message_id,
                    "primary_label": classification.primary_label,
                },
            )
            return classification
        _record("db_write", stage_start)

        if classification.primary_label == "Job Apps":
            stage_start = time.perf_counter()
            await _dispatch_job_apps(message_id)
            _record("dispatch", stage_start)

        return classification
    except Exception as exc:
        logger.warning(
            "triager.failed",
            extra={
                "message_id": message_id,
                "error_type": type(exc).__name__,
                "error_msg": str(exc)[:200],
            },
        )
        await _record_failure(
            message_id,
            exc,
            sender=sender,
            subject=subject,
            email_received_at=email_received_at,
            stage_timings=stage_timings,
        )
        raise


async def _dispatch_job_apps(message_id: str) -> None:
    """Forward a Job-Apps-labeled message to the Job Apps service over HTTP.

    The Gmail Resource is intentionally not serialized across the HTTP
    boundary; Job Apps rebuilds its own client from the shared OAuth
    refresh token. dispatch_job_apps is non-raising by contract, and the
    try/except keeps the webhook's always-200 contract even if the client
    ever regresses (a leaked exception would make Pub/Sub retry forever).
    """
    from shared.http_client import dispatch_job_apps

    try:
        await dispatch_job_apps(message_id)
    except Exception:
        logger.exception("job_apps.dispatch_failed", extra={"message_id": message_id})


async def _load_existing(message_id: str) -> TriagerRun | None:
    async with get_session() as session:
        result = await session.execute(
            select(TriagerRun).where(TriagerRun.message_id == message_id)
        )
        return result.scalar_one_or_none()


async def _record_failure(
    message_id: str,
    exc: Exception,
    *,
    sender: str | None,
    subject: str | None,
    email_received_at: datetime | None,
    stage_timings: dict[str, int],
) -> None:
    """Write an errored audit row so the failure is visible and replayable.

    A failed message would otherwise vanish: the webhook advances past it
    and Pub/Sub gets a 200, so nothing redelivers. The errored row (null
    primary_label, populated error) surfaces on the dashboard and replays
    via /classify-email; the retry path deletes it before reprocessing.
    Best-effort and never raises, since audit bookkeeping must not mask
    the original error; skips the write when any row already exists so a
    success row is never clobbered.
    """
    try:
        async with get_session() as session:
            if await session.get(TriagerRun, message_id) is not None:
                return
            session.add(
                TriagerRun(
                    message_id=message_id,
                    email_received_at=email_received_at,
                    primary_label=None,
                    flagged=False,
                    subject=subject or None,
                    sender=sender or None,
                    error=f"{type(exc).__name__}: {str(exc)[:500]}",
                    stage_timings_ms=stage_timings or None,
                )
            )
            await session.commit()
    except IntegrityError:
        # Concurrent worker recorded this message first; nothing to add.
        pass
    except Exception:
        logger.exception("triager.failure_audit_failed", extra={"message_id": message_id})


__all__ = ["process_message"]
