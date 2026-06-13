"""Unit tests for the Gmail watch lifecycle.

The Gmail Resource and async DB session are mocked; tests pin protocol
shape (users.watch() arguments, upsert contents) rather than Gmail or
Postgres behavior.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from services.triager.watch import (
    WatchState,
    _expiration_to_datetime,
    renew_watch,
    start_watch,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _service(watch_response: dict[str, Any]) -> MagicMock:
    """Mock the chained Gmail Resource so users().watch().execute() returns watch_response."""
    service = MagicMock()
    watch_call = MagicMock()
    watch_call.execute.return_value = watch_response
    service.users.return_value.watch.return_value = watch_call
    return service


def _make_session() -> MagicMock:
    """Build an async-shaped session mock whose execute and commit return awaitables."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


def _patch_session(session: MagicMock) -> Any:
    """Patch get_session so each call returns a fresh async cm yielding session.

    A single asynccontextmanager instance can only be entered once, so the
    patched callable rebuilds one per invocation, letting tests await
    start_watch multiple times against the same patch.
    """

    @asynccontextmanager
    async def _fresh_cm() -> AsyncIterator[MagicMock]:
        yield session

    return patch(
        "services.triager.watch.get_session",
        side_effect=lambda: _fresh_cm(),
    )


# ----------------------------------------------------------------- WatchState


def test_watch_state_shape_round_trips_through_pydantic() -> None:
    expires_at = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
    state = WatchState(
        email="watch@example.com",
        current_history_id=987654,
        expires_at=expires_at,
    )

    assert state.email == "watch@example.com"
    assert state.current_history_id == 987654
    assert state.expires_at == expires_at

    # The JSON-friendly dump is what the Modal one-shot returns.
    dumped = state.model_dump(mode="json")
    assert dumped["email"] == "watch@example.com"
    assert dumped["current_history_id"] == 987654
    assert dumped["expires_at"].startswith("2026-05-11")


def test_expiration_to_datetime_accepts_string_and_int() -> None:
    epoch_ms = 1778500800000  # 2026-05-11T12:00:00Z
    expected = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)

    assert _expiration_to_datetime(epoch_ms) == expected
    assert _expiration_to_datetime(str(epoch_ms)) == expected


# ----------------------------------------------------------------- start_watch


async def test_start_watch_calls_users_watch_with_topic_and_inbox_filter() -> None:
    service = _service(
        {
            "historyId": "12345",
            "expiration": "1778500800000",  # 2026-05-11T12:00:00Z
        }
    )
    session = _make_session()

    with _patch_session(session):
        state = await start_watch(service=service)

    watch_mock = service.users.return_value.watch
    assert watch_mock.call_count == 1
    call_kwargs = watch_mock.call_args.kwargs
    assert call_kwargs["userId"] == "me"

    body = call_kwargs["body"]
    # The topic must come from settings, pinned by tests/conftest.py.
    assert body["topicName"] == "projects/test-project/topics/test-topic"
    assert body["labelIds"] == ["INBOX"]
    assert body["labelFilterBehavior"] == "INCLUDE"

    # Returned WatchState reflects the API response.
    assert isinstance(state, WatchState)
    assert state.email == "watch@example.com"
    assert state.current_history_id == 12345
    assert state.expires_at == datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)


async def test_start_watch_writes_history_id_and_expiration_to_db() -> None:
    service = _service(
        {
            "historyId": "777",
            "expiration": "1778500800000",
        }
    )
    session = _make_session()

    with _patch_session(session):
        await start_watch(service=service)

    assert session.execute.await_count == 1
    session.commit.assert_awaited_once()

    # The compiled INSERT ON CONFLICT statement carries our values.
    stmt = session.execute.await_args.args[0]
    compiled = stmt.compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "gmail_watch_state" in sql
    assert "ON CONFLICT" in sql
    # The insert tuple carries the new values; the conflict set is
    # covered by the renew_watch cursor test below.
    assert "watch@example.com" in sql
    assert "777" in sql


async def test_start_watch_is_idempotent_on_repeat() -> None:
    """Calling start_watch twice issues two upserts, rewriting the row each time."""
    service = _service(
        {
            "historyId": "101",
            "expiration": "1778500800000",
        }
    )
    session = _make_session()

    with _patch_session(session):
        first = await start_watch(service=service)
        second = await start_watch(service=service)

    assert service.users.return_value.watch.call_count == 2
    assert session.execute.await_count == 2
    assert session.commit.await_count == 2
    assert first == second  # Same response yields the same WatchState.


# ----------------------------------------------------------------- renew_watch


async def test_renew_watch_uses_same_users_watch_call() -> None:
    """Renewal is the same Gmail API call; the split is call-site intent."""
    service = _service(
        {
            "historyId": "9001",
            "expiration": "1778500800000",
        }
    )
    session = _make_session()

    with _patch_session(session):
        state = await renew_watch(service=service)

    watch_mock = service.users.return_value.watch
    assert watch_mock.call_count == 1
    body = watch_mock.call_args.kwargs["body"]
    assert body["topicName"] == "projects/test-project/topics/test-topic"
    assert body["labelIds"] == ["INBOX"]
    assert state.current_history_id == 9001
    assert session.execute.await_count == 1
    session.commit.assert_awaited_once()


async def test_renew_watch_on_conflict_leaves_cursor_untouched() -> None:
    """The DO UPDATE SET refreshes only the expiry fields, never the cursor.

    Overwriting current_history_id with the watch() response would jump
    the webhook's cursor past any unprocessed window.
    """
    service = _service(
        {
            "historyId": "55555",
            "expiration": "1778500800000",
        }
    )
    session = _make_session()

    with _patch_session(session):
        state = await renew_watch(service=service)

    # The returned state reflects the API response...
    assert state.current_history_id == 55555
    stmt = session.execute.await_args.args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    insert_clause, update_clause = sql.split("DO UPDATE SET", 1)
    # ...the cursor is only seeded by the insert tuple; on conflict the
    # set clause touches expiry bookkeeping alone.
    assert "55555" in insert_clause
    assert "expires_at" in update_clause
    assert "last_renewed_at" in update_clause
    assert "current_history_id" not in update_clause
