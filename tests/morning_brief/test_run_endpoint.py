"""Tests for the morning-brief-api /run-morning-brief endpoint."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.morning_brief.api import api
from services.morning_brief.orchestrator import BriefResult
from shared.settings import get_settings

client = TestClient(api)


def _result() -> BriefResult:
    return BriefResult(
        page_id="page-1",
        tasks_today=2,
        tasks_this_week=3,
        tasks_overdue=1,
        tasks_reschedule=0,
        emails_count=4,
        news_count=5,
        duration_s=1.2,
    )


@patch("services.morning_brief.api.run_with_metrics")
def test_run_returns_result(mock_run: MagicMock) -> None:
    mock_run.return_value = _result()
    response = client.post("/run-morning-brief", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["page_id"] == "page-1"
    assert body["tasks_today"] == 2
    assert body["news_count"] == 5


@patch("services.morning_brief.api.run_with_metrics")
def test_run_accepts_date_override(mock_run: MagicMock) -> None:
    from datetime import date

    mock_run.return_value = _result()
    response = client.post("/run-morning-brief", json={"date": "2026-06-04"})
    assert response.status_code == 200
    assert mock_run.call_args.kwargs["today"] == date(2026, 6, 4)


@patch("services.morning_brief.api.run_with_metrics")
def test_run_invalid_date_is_422(mock_run: MagicMock) -> None:
    response = client.post("/run-morning-brief", json={"date": "not-a-date"})
    assert response.status_code == 422
    mock_run.assert_not_called()


@patch("services.morning_brief.api.run_with_metrics")
def test_run_orchestrator_failure_is_503(mock_run: MagicMock) -> None:
    mock_run.side_effect = RuntimeError("boom")
    response = client.post("/run-morning-brief", json={})
    assert response.status_code == 503
    assert "morning brief failed" in response.json()["detail"]


@patch("services.morning_brief.api.run_with_metrics")
def test_run_requires_bearer_when_token_set(
    mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_run.return_value = _result()
    monkeypatch.setenv("AGENTS_API_TOKEN", "tok-123")
    get_settings.cache_clear()
    try:
        # No header is rejected before the orchestrator runs.
        denied = client.post("/run-morning-brief", json={})
        assert denied.status_code == 401
        mock_run.assert_not_called()
        # A correct bearer passes auth.
        allowed = client.post(
            "/run-morning-brief", json={}, headers={"Authorization": "Bearer tok-123"}
        )
        assert allowed.status_code == 200
    finally:
        get_settings.cache_clear()
