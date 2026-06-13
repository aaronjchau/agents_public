"""Tests for the POST /gmail-webhook Pub/Sub push receiver.

Mocks JWT verification, Gmail history.list, process_message, and the DB
session; exercises the protocol shape without touching external systems.
"""

from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Iterator

from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from services.triager.api import api
from services.triager.types import Classification
from shared.settings import get_settings

client = TestClient(api)


# Fixed JWT used by every test; the contents never matter because
# id_token.verify_oauth2_token is patched to return pre-baked claims.
_FAKE_JWT = "eyJhbGciOiJSUzI1NiJ9.fake.signature"
_BEARER = {"Authorization": f"Bearer {_FAKE_JWT}"}


def _envelope(*, email: str = "watch@example.com", history_id: int = 1234) -> dict[str, Any]:
    """Build a Pub/Sub push envelope around a Gmail notification."""
    payload = json.dumps({"emailAddress": email, "historyId": history_id}).encode("utf-8")
    return {
        "message": {
            "data": base64.b64encode(payload).decode("ascii"),
            "messageId": "pubsub-msg-1",
            "publishTime": "2026-05-04T19:00:00Z",
        },
        "subscription": "projects/x/subscriptions/y",
    }


def _good_claims() -> dict[str, Any]:
    return {
        "email": get_settings().gmail_pubsub_push_sa,
        "aud": get_settings().gmail_pubsub_audience,
    }


def _history_response(
    *added_message_ids: str,
    next_page_token: str | None = None,
) -> dict[str, Any]:
    return {
        "history": [
            {"id": str(i), "messagesAdded": [{"message": {"id": mid}}]}
            for i, mid in enumerate(added_message_ids, start=1)
        ],
        "historyId": "9999",
        **({"nextPageToken": next_page_token} if next_page_token else {}),
    }


def _gmail_service(*list_responses: dict[str, Any]) -> MagicMock:
    """MagicMock that returns the given history.list responses in order."""
    service = MagicMock()
    list_iter = iter(list_responses)
    list_call = MagicMock()
    list_call.execute.side_effect = lambda: next(list_iter)
    service.users.return_value.history.return_value.list.return_value = list_call
    return service


def _watch_state(history_id: int = 100) -> MagicMock:
    """Stand-in for a SQLAlchemy GmailWatchState row."""
    state = MagicMock()
    state.email = "watch@example.com"
    state.current_history_id = history_id
    return state


def _scalar_result(*, scalar_one_or_none: Any = None) -> MagicMock:
    """MagicMock for a SQLAlchemy Result with the given scalar value."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=scalar_one_or_none)
    return result


def _make_session(execute_returns: list[Any]) -> MagicMock:
    """Build a session whose successive execute calls return the given values."""
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_returns)
    return session


def _patch_get_session(sessions: list[MagicMock]) -> Any:
    """Patch services.triager.api.get_session to yield the listed sessions in order."""
    sessions_iter: Iterator[MagicMock] = iter(sessions)

    @asynccontextmanager
    async def _get_session() -> Any:
        yield next(sessions_iter)

    return patch("services.triager.api.get_session", new=_get_session)


def _compiled_sql(session: MagicMock) -> str:
    """Render the single statement executed on session with literal binds."""
    stmt = session.execute.await_args.args[0]
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# --------------------------------------------------------------- auth gating


def test_webhook_missing_authorization_header_returns_401() -> None:
    response = client.post("/gmail-webhook", json=_envelope())
    assert response.status_code == 401
    assert "Authorization" in response.json()["detail"]


def test_webhook_wrong_scheme_returns_401() -> None:
    response = client.post(
        "/gmail-webhook",
        json=_envelope(),
        headers={"Authorization": "Basic abc:def"},
    )
    assert response.status_code == 401


def test_webhook_invalid_jwt_returns_401() -> None:
    with patch(
        "services.triager.api.id_token.verify_oauth2_token",
        side_effect=ValueError("invalid signature"),
    ):
        response = client.post("/gmail-webhook", json=_envelope(), headers=_BEARER)
    assert response.status_code == 401
    assert "invalid Pub/Sub JWT" in response.json()["detail"]


def test_webhook_email_claim_mismatch_returns_401() -> None:
    bad_claims = {
        "email": "someone-else@iam.gserviceaccount.com",
        "aud": get_settings().gmail_pubsub_audience,
    }
    with patch("services.triager.api.id_token.verify_oauth2_token", return_value=bad_claims):
        response = client.post("/gmail-webhook", json=_envelope(), headers=_BEARER)
    assert response.status_code == 401
    assert "service account" in response.json()["detail"]


def test_cert_transport_is_cached_between_requests() -> None:
    """JWT verification reuses one Google-cert transport across deliveries."""
    from services.triager.api import _cert_transport

    assert _cert_transport() is _cert_transport()


# ------------------------------------------------------- envelope validation


def test_webhook_missing_message_data_returns_400() -> None:
    with patch("services.triager.api.id_token.verify_oauth2_token", return_value=_good_claims()):
        response = client.post(
            "/gmail-webhook",
            json={"message": {}, "subscription": "x"},
            headers=_BEARER,
        )
    assert response.status_code == 400


def test_webhook_data_not_json_returns_400() -> None:
    bad_envelope = {
        "message": {"data": base64.b64encode(b"not-json").decode("ascii")},
        "subscription": "x",
    }
    with patch("services.triager.api.id_token.verify_oauth2_token", return_value=_good_claims()):
        response = client.post("/gmail-webhook", json=bad_envelope, headers=_BEARER)
    assert response.status_code == 400


# ----------------------------------------------------------- happy path


def test_webhook_happy_path_classifies_two_messages_and_advances_cursor() -> None:
    state = _watch_state(history_id=100)
    service = _gmail_service(_history_response("m1", "m2"))

    # Session 1 serves the existence check; session 2 the cursor UPDATE.
    s1 = _make_session([_scalar_result(scalar_one_or_none=state)])
    s2 = _make_session([MagicMock()])

    classification = Classification(primary_label="News", flagged=False, reasoning="newsletter")

    with (
        patch("services.triager.api.id_token.verify_oauth2_token", return_value=_good_claims()),
        patch("services.triager.api.build_gmail_service", return_value=service),
        patch(
            "services.triager.api.process_message",
            new=AsyncMock(return_value=classification),
        ) as mock_process,
        _patch_get_session([s1, s2]),
    ):
        response = client.post(
            "/gmail-webhook",
            json=_envelope(history_id=200),
            headers=_BEARER,
        )

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "processed": 2, "failed": 0, "found": 2}

    # process_message runs once per added message id, in order.
    assert mock_process.await_count == 2
    awaited_ids = [call.args[0] for call in mock_process.await_args_list]
    assert awaited_ids == ["m1", "m2"]

    # All process_message calls share one gmail service (batch reuse).
    awaited_services = [call.kwargs["service"] for call in mock_process.await_args_list]
    assert awaited_services == [service, service]

    # The cursor advances via the UPDATE carrying the notification's historyId.
    sql = _compiled_sql(s2)
    assert "UPDATE gmail_watch_state" in sql
    assert "200" in sql
    s2.commit.assert_awaited_once()


def test_webhook_history_list_pagination_collects_across_pages() -> None:
    """nextPageToken triggers a follow-up history.list call."""
    state = _watch_state(history_id=100)
    service = _gmail_service(
        _history_response("m1", next_page_token="page2"),
        _history_response("m2"),
    )
    s1 = _make_session([_scalar_result(scalar_one_or_none=state)])
    s2 = _make_session([MagicMock()])

    classification = Classification(primary_label="News", flagged=False, reasoning="ok")

    with (
        patch("services.triager.api.id_token.verify_oauth2_token", return_value=_good_claims()),
        patch("services.triager.api.build_gmail_service", return_value=service),
        patch(
            "services.triager.api.process_message",
            new=AsyncMock(return_value=classification),
        ) as mock_process,
        _patch_get_session([s1, s2]),
    ):
        response = client.post(
            "/gmail-webhook",
            json=_envelope(history_id=200),
            headers=_BEARER,
        )

    assert response.status_code == 200
    assert response.json()["found"] == 2
    assert mock_process.await_count == 2

    # Second history.list call should include pageToken=page2.
    list_call = service.users.return_value.history.return_value.list
    second_call_kwargs = list_call.call_args_list[1].kwargs
    assert second_call_kwargs.get("pageToken") == "page2"


def test_webhook_cursor_advance_is_one_atomic_greatest_update() -> None:
    """The cursor advance is one atomic GREATEST UPDATE.

    Monotonicity enforced in SQL means neither out-of-order notifications
    nor racing webhooks can commit a rewind via a stale Python-side read.
    """
    state = _watch_state(history_id=300)
    service = _gmail_service(_history_response("m1"))
    s1 = _make_session([_scalar_result(scalar_one_or_none=state)])
    s2 = _make_session([MagicMock()])

    classification = Classification(primary_label="News", flagged=False, reasoning="ok")

    with (
        patch("services.triager.api.id_token.verify_oauth2_token", return_value=_good_claims()),
        patch("services.triager.api.build_gmail_service", return_value=service),
        patch(
            "services.triager.api.process_message",
            new=AsyncMock(return_value=classification),
        ),
        _patch_get_session([s1, s2]),
    ):
        response = client.post(
            "/gmail-webhook",
            json=_envelope(history_id=200),
            headers=_BEARER,
        )

    assert response.status_code == 200
    # One statement, no read-modify-write.
    s2.execute.assert_awaited_once()
    sql = _compiled_sql(s2)
    assert "UPDATE gmail_watch_state" in sql
    assert "greatest(gmail_watch_state.current_history_id, 200)" in sql
    assert "watch@example.com" in sql
    s2.commit.assert_awaited_once()


def test_webhook_history_list_is_scoped_to_inbox_message_adds() -> None:
    """history.list must filter to INBOX messageAdded records.

    Without labelId=INBOX, sent mail gets fetched and classified on the
    next inbound notification.
    """
    state = _watch_state(history_id=100)
    service = _gmail_service(_history_response("m1"))
    s1 = _make_session([_scalar_result(scalar_one_or_none=state)])
    s2 = _make_session([MagicMock()])

    classification = Classification(primary_label="News", flagged=False, reasoning="ok")

    with (
        patch("services.triager.api.id_token.verify_oauth2_token", return_value=_good_claims()),
        patch("services.triager.api.build_gmail_service", return_value=service),
        patch(
            "services.triager.api.process_message",
            new=AsyncMock(return_value=classification),
        ),
        _patch_get_session([s1, s2]),
    ):
        response = client.post(
            "/gmail-webhook",
            json=_envelope(history_id=200),
            headers=_BEARER,
        )

    assert response.status_code == 200
    list_kwargs = service.users.return_value.history.return_value.list.call_args.kwargs
    assert list_kwargs.get("labelId") == "INBOX"
    assert list_kwargs.get("historyTypes") == ["messageAdded"]


def test_webhook_one_message_failing_does_not_block_batch() -> None:
    state = _watch_state(history_id=100)
    service = _gmail_service(_history_response("good1", "bad", "good2"))

    s1 = _make_session([_scalar_result(scalar_one_or_none=state)])
    s2 = _make_session([MagicMock()])

    classification = Classification(primary_label="Marketing", flagged=False, reasoning="ok")

    async def per_message(msg_id: str, *, service: Any = None) -> Classification:
        if msg_id == "bad":
            raise RuntimeError("classify exploded for this one")
        return classification

    with (
        patch("services.triager.api.id_token.verify_oauth2_token", return_value=_good_claims()),
        patch("services.triager.api.build_gmail_service", return_value=service),
        patch("services.triager.api.process_message", new=AsyncMock(side_effect=per_message)),
        _patch_get_session([s1, s2]),
    ):
        response = client.post(
            "/gmail-webhook",
            json=_envelope(history_id=300),
            headers=_BEARER,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["found"] == 3
    assert body["processed"] == 2  # The bad one was caught and skipped.
    # The cursor still advances even when one message failed.
    assert "300" in _compiled_sql(s2)
    s2.commit.assert_awaited_once()


def test_webhook_first_time_initializes_watch_state() -> None:
    """A first-ever webhook initializes gmail_watch_state from the notification.

    The INSERT carries ON CONFLICT DO NOTHING so the loser of two
    concurrent first-ever webhooks does not 500.
    """
    # Session 1 returns no row (initialization branch); session 2 inserts.
    s1 = _make_session([_scalar_result(scalar_one_or_none=None)])
    s2 = _make_session([MagicMock()])

    with (
        patch("services.triager.api.id_token.verify_oauth2_token", return_value=_good_claims()),
        patch("services.triager.api.build_gmail_service") as mock_build,
        patch("services.triager.api.process_message", new=AsyncMock()) as mock_process,
        _patch_get_session([s1, s2]),
    ):
        response = client.post(
            "/gmail-webhook",
            json=_envelope(history_id=500),
            headers=_BEARER,
        )

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "initialized", "processed": 0}

    # On first-time init, no Gmail fetch and no per-message processing.
    mock_build.assert_not_called()
    mock_process.assert_not_awaited()

    # The watch row was inserted race-tolerantly in session 2.
    s2.execute.assert_awaited_once()
    sql = _compiled_sql(s2)
    assert "INSERT INTO gmail_watch_state" in sql
    assert "ON CONFLICT (email) DO NOTHING" in sql
    assert "watch@example.com" in sql
    assert "500" in sql
    s2.commit.assert_awaited_once()


def test_webhook_history_list_failure_returns_200_without_advancing() -> None:
    """A history.list failure still returns 200 and leaves the cursor put.

    Non-2xx would trigger Pub/Sub redelivery, which does not help a
    transient Gmail blip; the next notification re-covers the gap.
    """
    state = _watch_state(history_id=100)
    s1 = _make_session([_scalar_result(scalar_one_or_none=state)])

    with (
        patch("services.triager.api.id_token.verify_oauth2_token", return_value=_good_claims()),
        patch(
            "services.triager.api.build_gmail_service",
            side_effect=RuntimeError("gmail down"),
        ),
        patch("services.triager.api.process_message", new=AsyncMock()) as mock_process,
        _patch_get_session([s1]),
    ):
        response = client.post(
            "/gmail-webhook",
            json=_envelope(history_id=200),
            headers=_BEARER,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "history_list_failed"
    mock_process.assert_not_awaited()
    # The cursor stays at the original 100, not advanced.
    assert state.current_history_id == 100


def test_webhook_stale_cursor_404_reseeds_cursor_from_notification() -> None:
    """A history.list 404 (stale cursor) re-seeds the cursor from the notification.

    Retrying the same cursor would 404 forever, so the webhook self-heals
    via the same atomic GREATEST update while keeping the always-200
    contract.
    """
    state = _watch_state(history_id=100)
    resp = MagicMock()
    resp.status = 404
    resp.reason = "Not Found"
    err = HttpError(resp, b'{"error": "history not found"}')
    service = MagicMock()
    service.users.return_value.history.return_value.list.return_value.execute.side_effect = err

    s1 = _make_session([_scalar_result(scalar_one_or_none=state)])
    s2 = _make_session([MagicMock()])

    with (
        patch("services.triager.api.id_token.verify_oauth2_token", return_value=_good_claims()),
        patch("services.triager.api.build_gmail_service", return_value=service),
        patch("services.triager.api.process_message", new=AsyncMock()) as mock_process,
        _patch_get_session([s1, s2]),
    ):
        response = client.post(
            "/gmail-webhook",
            json=_envelope(history_id=99999),
            headers=_BEARER,
        )

    assert response.status_code == 200
    assert response.json() == {"status": "history_cursor_reset", "processed": 0}
    mock_process.assert_not_awaited()

    # The cursor was re-seeded to the notification's historyId via GREATEST.
    s2.execute.assert_awaited_once()
    sql = _compiled_sql(s2)
    assert "UPDATE gmail_watch_state" in sql
    assert "greatest(gmail_watch_state.current_history_id, 99999)" in sql
    s2.commit.assert_awaited_once()


def test_webhook_non_404_http_error_leaves_cursor_unchanged() -> None:
    """A non-404 HttpError is transient: the cursor stays put, no reset.

    Only the documented stale-cursor 404 triggers re-seeding.
    """
    state = _watch_state(history_id=100)
    resp = MagicMock()
    resp.status = 500
    resp.reason = "Internal Server Error"
    err = HttpError(resp, b'{"error": "boom"}')
    service = MagicMock()
    service.users.return_value.history.return_value.list.return_value.execute.side_effect = err

    s1 = _make_session([_scalar_result(scalar_one_or_none=state)])

    with (
        patch("services.triager.api.id_token.verify_oauth2_token", return_value=_good_claims()),
        patch("services.triager.api.build_gmail_service", return_value=service),
        patch("services.triager.api.process_message", new=AsyncMock()) as mock_process,
        _patch_get_session([s1]),
    ):
        response = client.post(
            "/gmail-webhook",
            json=_envelope(history_id=200),
            headers=_BEARER,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "history_list_failed"
    mock_process.assert_not_awaited()
    # Only the read session was used; no UPDATE was issued.
    assert state.current_history_id == 100
