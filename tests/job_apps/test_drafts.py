"""Tests for the /api/dashboard/job-apps/drafts endpoint.

Gmail and the audit-row lookup are mocked; an opt-in live test
(AGENTS_LIVE_GMAIL=1) checks the real Drafts API shape.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from services.job_apps import api as api_module
from services.job_apps.api import api
from services.job_apps.dashboard_models import (
    JobAppsDraftRow,
    JobAppsDraftsResponse,
)
from services.job_apps.dashboard_queries import fetch_runs_by_message_ids
from shared.db import session_dependency
from shared.settings import get_settings

client = TestClient(api)


# ---------------------------------------------------------------------------
# Settings hygiene: every test starts without AGENTS_API_TOKEN so the
# bearer dep passes through; test_bearer_enforced opts in explicitly.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("AGENTS_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _override_session() -> Any:
    """Swap the DB dependency for a no-op MagicMock.

    The lookup itself is mocked per test, but something must satisfy
    the dependency or FastAPI tries to build a real engine.
    """

    async def _fake_session() -> Any:
        yield MagicMock(spec=AsyncSession)

    api.dependency_overrides[session_dependency] = _fake_session
    yield
    api.dependency_overrides.pop(session_dependency, None)


# ---------------------------------------------------------------------------
# /drafts (mocked Gmail)
# ---------------------------------------------------------------------------


def _fake_gmail_service(
    drafts_payload: list[dict[str, Any]],
    drafts_by_id: dict[str, dict[str, Any]],
    *,
    raise_on_get: str | None = None,
    raise_status: int = 404,
) -> MagicMock:
    """Build a MagicMock that mimics the chained google-api-client surface."""
    service = MagicMock()
    drafts_resource = MagicMock()
    service.users.return_value.drafts.return_value = drafts_resource
    drafts_resource.list.return_value.execute.return_value = {"drafts": drafts_payload}

    def _get_impl(**kwargs: Any) -> MagicMock:
        draft_id = kwargs["id"]
        if draft_id == raise_on_get:
            from googleapiclient.errors import HttpError

            raise HttpError(MagicMock(status=raise_status, reason="error"), b"")
        executor = MagicMock()
        executor.execute.return_value = drafts_by_id[draft_id]
        return executor

    drafts_resource.get.side_effect = _get_impl
    return service


def test_drafts_joins_audit_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    drafts_list = [{"id": "d1", "message": {"id": "m1"}}, {"id": "d2", "message": {"id": "m2"}}]
    drafts_full = {
        "d1": {
            "id": "d1",
            "message": {
                "id": "m1",
                "internalDate": str(
                    int(datetime(2026, 5, 15, 12, 0, tzinfo=UTC).timestamp() * 1000)
                ),
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Re: phone screen"},
                        {"name": "From", "value": "Recruiter <rec@bigco.com>"},
                    ]
                },
            },
        },
        "d2": {
            "id": "d2",
            "message": {
                "id": "m2",
                "internalDate": str(
                    int(datetime(2026, 5, 16, 12, 0, tzinfo=UTC).timestamp() * 1000)
                ),
                "payload": {"headers": []},
            },
        },
    }
    service = _fake_gmail_service(drafts_list, drafts_full)
    monkeypatch.setattr(api_module, "build_gmail_service", lambda: service)

    # Fake the audit-row join. Return a sublabel only for m1.
    class _FakeAudit:
        sublabel = "Interview Scheduling"

    async def fake_lookup(_session: Any, message_ids: list[str]) -> dict[str, Any]:
        assert sorted(message_ids) == ["m1", "m2"]
        return {"m1": _FakeAudit()}

    monkeypatch.setattr(api_module, "fetch_runs_by_message_ids", fake_lookup)

    response = client.get("/api/dashboard/job-apps/drafts")
    assert response.status_code == 200
    body = JobAppsDraftsResponse.model_validate(response.json())
    by_draft = {d.draft_id: d for d in body.drafts}

    # Sorted newest first.
    assert [d.draft_id for d in body.drafts] == ["d2", "d1"]

    assert by_draft["d1"].subject == "Re: phone screen"
    assert by_draft["d1"].sender == "Recruiter <rec@bigco.com>"
    assert by_draft["d1"].sublabel == "Interview Scheduling"
    assert by_draft["d1"].message_id == "m1"
    assert by_draft["d1"].gmail_deep_link.endswith("#drafts/d1")

    assert by_draft["d2"].subject is None
    assert by_draft["d2"].sublabel is None  # No audit row.


def test_drafts_skips_404_get(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drafts that 404 between list and get are filtered, not fatal."""
    drafts_list = [
        {"id": "d_gone", "message": {"id": "m_gone"}},
        {"id": "d_ok", "message": {"id": "m_ok"}},
    ]
    drafts_full = {
        "d_ok": {
            "id": "d_ok",
            "message": {
                "id": "m_ok",
                "payload": {"headers": [{"name": "Subject", "value": "alive"}]},
            },
        },
    }
    service = _fake_gmail_service(drafts_list, drafts_full, raise_on_get="d_gone")
    monkeypatch.setattr(api_module, "build_gmail_service", lambda: service)

    async def fake_lookup(_session: Any, _ids: list[str]) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(api_module, "fetch_runs_by_message_ids", fake_lookup)

    response = client.get("/api/dashboard/job-apps/drafts")
    assert response.status_code == 200
    body = JobAppsDraftsResponse.model_validate(response.json())
    assert [d.draft_id for d in body.drafts] == ["d_ok"]


def test_drafts_non_404_get_surfaces_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only per-draft 404s are skipped; a non-404 get fails the panel with 503."""
    drafts_list = [
        {"id": "d_err", "message": {"id": "m_err"}},
        {"id": "d_ok", "message": {"id": "m_ok"}},
    ]
    drafts_full = {
        "d_ok": {
            "id": "d_ok",
            "message": {
                "id": "m_ok",
                "payload": {"headers": [{"name": "Subject", "value": "alive"}]},
            },
        },
    }
    service = _fake_gmail_service(drafts_list, drafts_full, raise_on_get="d_err", raise_status=500)
    monkeypatch.setattr(api_module, "build_gmail_service", lambda: service)

    async def fake_lookup(_session: Any, _ids: list[str]) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(api_module, "fetch_runs_by_message_ids", fake_lookup)

    response = client.get("/api/dashboard/job-apps/drafts")
    assert response.status_code == 503
    assert "500" in response.json()["detail"]


def test_drafts_handles_list_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """503 surfaces to the dashboard when the live Gmail call blows up."""

    def fail() -> MagicMock:
        raise RuntimeError("network down")

    monkeypatch.setattr(api_module, "build_gmail_service", fail)
    response = client.get("/api/dashboard/job-apps/drafts")
    assert response.status_code == 503
    assert "RuntimeError" in response.json()["detail"]


def test_drafts_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _fake_gmail_service([], {})
    monkeypatch.setattr(api_module, "build_gmail_service", lambda: service)

    async def fake_lookup(_session: Any, message_ids: list[str]) -> dict[str, Any]:
        assert message_ids == []
        return {}

    monkeypatch.setattr(api_module, "fetch_runs_by_message_ids", fake_lookup)
    response = client.get("/api/dashboard/job-apps/drafts")
    assert response.status_code == 200
    assert response.json() == {"drafts": []}


# ---------------------------------------------------------------------------
# Helper-function unit tests
# ---------------------------------------------------------------------------


def test_draft_subject_sender_case_insensitive() -> None:
    """From arrives in mixed case for forwarded mail; match regardless."""
    payload = {
        "headers": [
            {"name": "from", "value": "lowercase@x.com"},
            {"name": "SUBJECT", "value": "shouty"},
        ]
    }
    subject, sender = api_module._draft_subject_sender(payload)
    assert subject == "shouty"
    assert sender == "lowercase@x.com"


def test_draft_subject_sender_handles_missing() -> None:
    subject, sender = api_module._draft_subject_sender({"headers": []})
    assert (subject, sender) == (None, None)


def test_draft_created_at_prefers_internal_date() -> None:
    ts_ms = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    msg = {
        "internalDate": str(ts_ms),
        "payload": {"headers": [{"name": "Date", "value": "Mon, 02 Jan 2026 00:00:00 +0000"}]},
    }
    got = api_module._draft_created_at(msg)
    assert got == datetime(2026, 1, 1, tzinfo=UTC)


def test_draft_created_at_falls_back_to_header() -> None:
    msg = {"payload": {"headers": [{"name": "Date", "value": "Mon, 02 Jan 2026 12:00:00 +0000"}]}}
    got = api_module._draft_created_at(msg)
    assert got == datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


def test_draft_created_at_returns_none_when_unavailable() -> None:
    assert api_module._draft_created_at({"payload": {"headers": []}}) is None


# ---------------------------------------------------------------------------
# Bearer auth: drafts is the only guarded /api/dashboard/* route.
# ---------------------------------------------------------------------------


def test_bearer_enforced_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTS_API_TOKEN", "sk-secret")
    get_settings.cache_clear()

    # Stub both upstreams so a happy 200 path exists when the bearer is
    # correct; the guard is under test, not the drafts handler.
    service = _fake_gmail_service([], {})
    monkeypatch.setattr(api_module, "build_gmail_service", lambda: service)

    async def fake_lookup(_session: Any, _ids: list[str]) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(api_module, "fetch_runs_by_message_ids", fake_lookup)

    # A missing bearer returns 401.
    assert client.get("/api/dashboard/job-apps/drafts").status_code == 401

    # A wrong bearer returns 401.
    headers = {"Authorization": "Bearer wrong"}
    assert client.get("/api/dashboard/job-apps/drafts", headers=headers).status_code == 401

    # The correct bearer returns 200.
    headers = {"Authorization": "Bearer sk-secret"}
    assert client.get("/api/dashboard/job-apps/drafts", headers=headers).status_code == 200


# ---------------------------------------------------------------------------
# Query-helper unit test: the bulk audit-row lookup the drafts join uses.
# ---------------------------------------------------------------------------


async def test_runs_by_message_ids_empty_short_circuits() -> None:
    """Empty list returns empty dict without issuing SQL."""
    fake_session = MagicMock(spec=AsyncSession)
    fake_session.scalars = MagicMock()  # Should not be called.

    result = await fetch_runs_by_message_ids(fake_session, [])
    assert result == {}
    fake_session.scalars.assert_not_called()


# ---------------------------------------------------------------------------
# Opt-in live Gmail test; verifies the real Drafts API still returns the
# expected shape. Skipped unless AGENTS_LIVE_GMAIL=1.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AGENTS_LIVE_GMAIL") != "1",
    reason="set AGENTS_LIVE_GMAIL=1 to hit real Gmail Drafts API",
)
def test_drafts_endpoint_live_gmail(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live Gmail API still matches the parsing assumptions.

    The audit-row lookup is stubbed so no live Postgres is needed.
    """

    async def fake_lookup(_session: Any, _ids: list[str]) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(api_module, "fetch_runs_by_message_ids", fake_lookup)

    response = client.get("/api/dashboard/job-apps/drafts")
    assert response.status_code == 200
    body = JobAppsDraftsResponse.model_validate(response.json())
    # Draft contents vary; assert only well-formed rows and deep links.
    for draft in body.drafts:
        assert isinstance(draft, JobAppsDraftRow)
        assert draft.gmail_deep_link.startswith("https://mail.google.com")
