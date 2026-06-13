"""Gmail Pub/Sub watch lifecycle for the triager.

users.watch() registers the push topic and must be renewed at least every
seven days; state lives in gmail_watch_state, one row per mailbox.
Design notes: docs/design.md.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert

from shared.db import GmailWatchState, get_session
from shared.gmail import build_gmail_service
from shared.settings import get_settings

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

logger = logging.getLogger(__name__)


class WatchState(BaseModel):
    """Pydantic projection of one gmail_watch_state row, JSON-serializable for endpoints."""

    email: str
    current_history_id: int
    expires_at: datetime


async def start_watch(*, service: Resource | None = None) -> WatchState:
    """Register (or reset) the Gmail push watch for settings.gmail_watch_email.

    Watches INBOX only; Sent and Drafts are never classified. Seeds
    gmail_watch_state on first registration; on an existing row only the
    expiration fields are refreshed (the webhook owns the cursor), so the
    persisted cursor may be older than the returned current_history_id.
    Idempotent: every call resets the watch with a fresh expiration.
    """
    state = await _watch_and_persist(service=service)
    logger.info(
        "gmail_watch.started",
        extra={"email": state.email, "history_id": state.current_history_id},
    )
    return state


async def renew_watch(*, service: Resource | None = None) -> WatchState:
    """Renew the existing watch; the same API call as start_watch.

    Gmail uses users.watch() for both registration and renewal. The split
    exists so the renewal cron's log lines state intent.
    """
    state = await _watch_and_persist(service=service)
    logger.info(
        "gmail_watch.renewed",
        extra={"email": state.email, "history_id": state.current_history_id},
    )
    return state


async def _watch_and_persist(*, service: Resource | None) -> WatchState:
    """Shared implementation for start_watch and renew_watch."""
    settings = get_settings()
    api = service or build_gmail_service()

    body = {
        "topicName": settings.gmail_pubsub_topic,
        "labelIds": ["INBOX"],
        # labelFilterBehavior is the current API; the older
        # labelFilterAction was deprecated.
        "labelFilterBehavior": "INCLUDE",
    }
    response: dict[str, Any] = api.users().watch(userId="me", body=body).execute()

    history_id = int(response["historyId"])
    expires_at = _expiration_to_datetime(response["expiration"])
    email = settings.gmail_watch_email

    async with get_session() as session:
        stmt = insert(GmailWatchState).values(
            email=email,
            current_history_id=history_id,
            expires_at=expires_at,
        )
        # On conflict, never touch current_history_id: the cursor is the
        # webhook's replay handle. users.watch() returns the mailbox's
        # current historyId, and overwriting the stored cursor with it
        # would silently jump past any unprocessed window, exactly the
        # window the webhook's history.list-failure path (cursor stays put,
        # next notification re-covers the gap) deliberately preserves.
        stmt = stmt.on_conflict_do_update(
            index_elements=[GmailWatchState.email],
            set_={
                "expires_at": expires_at,
                "last_renewed_at": datetime.now(tz=UTC),
            },
        )
        await session.execute(stmt)
        await session.commit()

    return WatchState(
        email=email,
        current_history_id=history_id,
        expires_at=expires_at,
    )


def _expiration_to_datetime(raw: str | int) -> datetime:
    """Convert Gmail's expiration (epoch milliseconds, string or int) to a datetime."""
    return datetime.fromtimestamp(int(raw) / 1000, tz=UTC)
