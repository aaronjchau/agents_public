"""Async SQL helper backing the /job-apps/drafts endpoint.

The drafts handler is the only dashboard view served through FastAPI;
this module exposes the bulk audit-row lookup its live Gmail join needs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from shared.db import JobAppsRun

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def fetch_runs_by_message_ids(
    session: AsyncSession, message_ids: list[str]
) -> dict[str, JobAppsRun]:
    """Bulk-load audit rows keyed by message_id for the drafts join.

    Empty input returns an empty dict; Postgres rejects a
    WHERE message_id IN () query.
    """
    if not message_ids:
        return {}
    stmt = select(JobAppsRun).where(JobAppsRun.message_id.in_(message_ids))
    result = await session.scalars(stmt)
    return {row.message_id: row for row in result.all()}


__all__ = ["fetch_runs_by_message_ids"]
