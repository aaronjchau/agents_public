"""Tests for the POST /run-job-apps manual replay endpoint.

The endpoint only maps runner exceptions to HTTP statuses; pipeline
behavior is covered by test_runner.py and test_graph.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from services.job_apps.api import api
from services.job_apps.graph import JobAppsState
from services.job_apps.types import MatchResult, WriteResult
from shared.settings import get_settings

client = TestClient(api)


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Start every test without AGENTS_API_TOKEN so the bearer dep passes through."""
    monkeypatch.delenv("AGENTS_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _job_apps_state(
    *,
    message_id: str = "m-1",
    sublabel: str = "Interview Scheduling",
    match_status: str = "matched",
    notion_row_id: str = "row-7",
    terminal_reason: str | None = None,
    status_changed: bool = True,
    new_status: str | None = "Screen",
) -> JobAppsState:
    """Build a representative final state for endpoint-shape assertions."""
    return JobAppsState(
        message_id=message_id,
        sublabel=sublabel,  # type: ignore[arg-type]
        match_result=MatchResult(
            status=match_status,  # type: ignore[arg-type]
            notion_row_id=notion_row_id,
        ),
        notion_row_id=notion_row_id,
        notion_write_result=WriteResult(
            status_changed=status_changed,
            new_status=new_status,
        ),
        terminal_reason=terminal_reason,
    )


def test_run_job_apps_happy_path_returns_flattened_state() -> None:
    """A successful run returns 200 with the full RunJobAppsResponse shape."""
    state = _job_apps_state()
    with patch(
        "services.job_apps.api.process_job_apps_message",
        new=AsyncMock(return_value=state),
    ) as mock_runner:
        response = client.post("/run-job-apps", json={"message_id": "m-1"})

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "message_id": "m-1",
        "sublabel": "Interview Scheduling",
        "match_status": "matched",
        "notion_row_id": "row-7",
        "status_changed": True,
        "new_status": "Screen",
        "terminal_reason": None,
        "errors": [],
    }
    mock_runner.assert_awaited_once_with("m-1", raise_on_failure=True)


def test_run_job_apps_terminal_offer_path_flattened_correctly() -> None:
    """Offer terminal path: no match, no row written."""
    state = JobAppsState(
        message_id="m-offer",
        sublabel="Offer",
        terminal_reason="flagged_offer",
    )
    with patch(
        "services.job_apps.api.process_job_apps_message",
        new=AsyncMock(return_value=state),
    ):
        response = client.post("/run-job-apps", json={"message_id": "m-offer"})

    assert response.status_code == 200
    body = response.json()
    assert body["sublabel"] == "Offer"
    assert body["terminal_reason"] == "flagged_offer"
    assert body["match_status"] is None
    assert body["notion_row_id"] is None
    assert body["status_changed"] is False
    assert body["new_status"] is None


def test_run_job_apps_returns_404_when_gmail_says_not_found() -> None:
    """A Gmail HttpError 404 maps to HTTP 404."""
    resp = MagicMock()
    resp.status = 404
    resp.reason = "Not Found"
    err = HttpError(resp, b'{"error": "not found"}')
    with patch(
        "services.job_apps.api.process_job_apps_message",
        new=AsyncMock(side_effect=err),
    ):
        response = client.post("/run-job-apps", json={"message_id": "missing"})

    assert response.status_code == 404
    assert "missing" in response.json()["detail"]


def test_run_job_apps_non_404_http_error_returns_503() -> None:
    """A non-404 HttpError maps to HTTP 503."""
    resp = MagicMock()
    resp.status = 500
    resp.reason = "Internal Server Error"
    err = HttpError(resp, b'{"error": "boom"}')
    with patch(
        "services.job_apps.api.process_job_apps_message",
        new=AsyncMock(side_effect=err),
    ):
        response = client.post("/run-job-apps", json={"message_id": "id"})

    assert response.status_code == 503
    assert "HttpError" in response.json()["detail"]


def test_run_job_apps_generic_exception_returns_503() -> None:
    """Anthropic / Notion / DB failure surfaces as HTTP 503."""
    with patch(
        "services.job_apps.api.process_job_apps_message",
        new=AsyncMock(side_effect=RuntimeError("anthropic blew up")),
    ):
        response = client.post("/run-job-apps", json={"message_id": "m"})

    assert response.status_code == 503
    assert "RuntimeError" in response.json()["detail"]


def test_run_job_apps_missing_message_id_returns_422() -> None:
    """FastAPI's request validation rejects an empty body."""
    response = client.post("/run-job-apps", json={})
    assert response.status_code == 422


def test_run_job_apps_bearer_enforced_when_token_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting the verify_bearer wiring on the route must fail this test."""
    monkeypatch.setenv("AGENTS_API_TOKEN", "sk-secret")
    get_settings.cache_clear()

    state = _job_apps_state()
    with patch(
        "services.job_apps.api.process_job_apps_message",
        new=AsyncMock(return_value=state),
    ):
        # A missing bearer returns 401.
        assert client.post("/run-job-apps", json={"message_id": "m-1"}).status_code == 401

        # A wrong bearer returns 401.
        headers = {"Authorization": "Bearer wrong"}
        response = client.post("/run-job-apps", json={"message_id": "m-1"}, headers=headers)
        assert response.status_code == 401

        # The correct bearer returns 200.
        headers = {"Authorization": "Bearer sk-secret"}
        response = client.post("/run-job-apps", json={"message_id": "m-1"}, headers=headers)
        assert response.status_code == 200
