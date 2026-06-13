"""Async helpers for the email_received_at one-shot backfill.

Lives under shared/ (not scripts/) so the Modal image can import it;
add_local_python_source only picks up the shared package. The companion
scripts/backfill_email_received_at.py is a thin CLI wrapper around run().
Idempotent: only touches rows where email_received_at is NULL.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from shared.db import JobAppsRun, TriagerRun, get_session
from shared.gmail import build_gmail_service, parse_internal_date

if TYPE_CHECKING:
    from datetime import datetime

    from googleapiclient.discovery import Resource

logger = logging.getLogger(__name__)


def _fetch_internal_date(service: Resource, message_id: str) -> datetime | None:
    """Look up Gmail's internalDate for one message.

    format='metadata' costs 5 quota units versus a full fetch; only the
    top-level internalDate is needed. Returns None when the message has
    been deleted from Gmail; the caller leaves the column at NULL.
    """
    try:
        message = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata", metadataHeaders=[])
            .execute()
        )
    except Exception:
        logger.warning("backfill.fetch_failed", extra={"message_id": message_id})
        return None
    return parse_internal_date(message.get("internalDate"))


async def _backfill_table(
    service: Resource, model: type[TriagerRun] | type[JobAppsRun]
) -> tuple[int, int]:
    """Fill in email_received_at where NULL for one audit table.

    Returns (updated_count, skipped_count); skipped covers messages
    deleted from Gmail or with an unparseable internalDate.
    """
    async with get_session() as session:
        stmt = select(model.message_id).where(model.email_received_at.is_(None))
        message_ids = list((await session.scalars(stmt)).all())

    updated = 0
    skipped = 0
    for message_id in message_ids:
        received_at = _fetch_internal_date(service, message_id)
        if received_at is None:
            skipped += 1
            continue
        async with get_session() as session:
            await session.execute(
                update(model)
                .where(model.message_id == message_id)
                .values(email_received_at=received_at)
            )
            await session.commit()
        updated += 1
    return updated, skipped


async def run() -> str:
    """Run the backfill across both audit tables.

    Builds one Gmail service and reuses it for every fetch so the OAuth
    credential exchange happens once.
    """
    service = build_gmail_service()
    t_updated, t_skipped = await _backfill_table(service, TriagerRun)
    j_updated, j_skipped = await _backfill_table(service, JobAppsRun)
    return (
        f"triager_runs: updated={t_updated} skipped={t_skipped} | "
        f"job_apps_runs: updated={j_updated} skipped={j_skipped}"
    )


__all__ = [
    "run",
]
