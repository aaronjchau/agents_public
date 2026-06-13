"""Tests for the POST /run-news-brief endpoint with the orchestrator mocked.

Exercises the FastAPI plumbing (request parsing, default-window ET math,
response shape, exception-to-503 mapping) without running the pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from services.news_brief.api import api
from services.news_brief.orchestrator import BriefResult
from shared.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

client = TestClient(api)
ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start every test without AGENTS_API_TOKEN so the bearer dep passes through.

    The 401 test opts in explicitly.
    """
    monkeypatch.delenv("AGENTS_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _result(
    page_id: str = "page-1", email_count: int = 5, story_count: int = 3, duration_s: float = 1.23
) -> BriefResult:
    return BriefResult(
        page_id=page_id,
        email_count=email_count,
        story_count=story_count,
        duration_s=duration_s,
    )


@patch("services.news_brief.api.run_with_metrics")
def test_run_endpoint_returns_run_result(mock_run: MagicMock) -> None:
    mock_run.return_value = _result()

    response = client.post("/run-news-brief", json={})

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "page_id": "page-1",
        "email_count": 5,
        "story_count": 3,
        "duration_s": 1.23,
    }


@patch("services.news_brief.api.run_with_metrics")
def test_run_endpoint_no_body_uses_defaults(mock_run: MagicMock) -> None:
    """A bare POST with no body should still default to 10 AM ET yesterday."""
    mock_run.return_value = _result()

    response = client.post("/run-news-brief")

    assert response.status_code == 200
    kwargs = mock_run.call_args.kwargs
    since = kwargs["since"]
    assert since.tzinfo is not None
    since_et = since.astimezone(ET)
    now_et = datetime.now(tz=ET)
    expected_yesterday = (now_et - timedelta(days=1)).date()
    assert since_et.date() == expected_yesterday
    assert since_et.hour == 10
    assert since_et.minute == 0
    assert kwargs["until"] is None


@patch("services.news_brief.api.run_with_metrics")
def test_run_endpoint_explicit_window(mock_run: MagicMock) -> None:
    mock_run.return_value = _result()
    since_iso = "2026-05-02T14:00:00+00:00"
    until_iso = "2026-05-03T13:15:00+00:00"

    response = client.post(
        "/run-news-brief",
        json={"since_iso": since_iso, "until_iso": until_iso},
    )

    assert response.status_code == 200
    kwargs = mock_run.call_args.kwargs
    assert kwargs["since"] == datetime(2026, 5, 2, 14, 0, tzinfo=UTC)
    assert kwargs["until"] == datetime(2026, 5, 3, 13, 15, tzinfo=UTC)


@patch("services.news_brief.api.run_with_metrics")
def test_run_endpoint_invalid_iso_returns_422(mock_run: MagicMock) -> None:
    response = client.post(
        "/run-news-brief",
        json={"since_iso": "not-a-date"},
    )
    assert response.status_code == 422
    mock_run.assert_not_called()


@patch("services.news_brief.api.run_with_metrics")
def test_run_endpoint_naive_datetime_returns_422(mock_run: MagicMock) -> None:
    """A naive datetime is rejected at validation instead of surfacing as a 503."""
    response = client.post(
        "/run-news-brief",
        json={"since_iso": "2026-05-02T14:00:00"},
    )
    assert response.status_code == 422
    mock_run.assert_not_called()


@patch("services.news_brief.api.run_with_metrics")
def test_run_endpoint_requires_bearer_when_token_set(
    mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With AGENTS_API_TOKEN set, wrong or missing bearers get 401, matching passes.

    Pins the Depends(verify_bearer) wiring, which the token-unset
    pass-through otherwise hides.
    """
    mock_run.return_value = _result()
    monkeypatch.setenv("AGENTS_API_TOKEN", "sk-secret")
    get_settings.cache_clear()

    assert client.post("/run-news-brief", json={}).status_code == 401

    headers = {"Authorization": "Bearer wrong"}
    assert client.post("/run-news-brief", json={}, headers=headers).status_code == 401

    headers = {"Authorization": "Bearer sk-secret"}
    assert client.post("/run-news-brief", json={}, headers=headers).status_code == 200


@patch("services.news_brief.api.run_with_metrics")
def test_run_endpoint_orchestrator_failure_returns_503(mock_run: MagicMock) -> None:
    """Any orchestrator exception maps to a 503 carrying the class name."""
    mock_run.side_effect = RuntimeError("anthropic upstream error")

    response = client.post("/run-news-brief", json={})

    assert response.status_code == 503
    body = response.json()
    assert "RuntimeError" in body["detail"]


@patch("services.news_brief.api.run_with_metrics")
def test_run_endpoint_propagates_arbitrary_exception_class(mock_run: MagicMock) -> None:
    """A non-stdlib exception class still surfaces in the 503 detail."""

    class NotionRateLimitError(Exception):
        pass

    mock_run.side_effect = NotionRateLimitError("429")

    response = client.post("/run-news-brief", json={})

    assert response.status_code == 503
    assert "NotionRateLimitError" in response.json()["detail"]
