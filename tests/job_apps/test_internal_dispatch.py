"""Tests for the POST /internal/dispatch cross-service endpoint.

Covers the receiving side of the Triager dispatch boundary: bearer
guard, 204 on success, 503 with a PII-free detail on failure.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from services.job_apps.api import api
from services.job_apps.graph import JobAppsState
from shared.settings import get_settings

client = TestClient(api)


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Start every test without AGENTS_API_TOKEN so the bearer dep passes through."""
    monkeypatch.delenv("AGENTS_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_internal_dispatch_returns_204_on_success() -> None:
    """A completed run returns 204 No Content."""
    state = JobAppsState(message_id="m-1")
    with patch(
        "services.job_apps.api.process_job_apps_message",
        new=AsyncMock(return_value=state),
    ) as mock_runner:
        response = client.post("/internal/dispatch", json={"message_id": "m-1"})

    assert response.status_code == 204
    assert response.content == b""
    mock_runner.assert_awaited_once_with("m-1")


def test_internal_dispatch_returns_503_on_runner_failure() -> None:
    """A runner raise returns 503 with only the exception class.

    The detail is never the message text, which can echo email PII.
    """
    with patch(
        "services.job_apps.api.process_job_apps_message",
        new=AsyncMock(side_effect=RuntimeError("graph blew up")),
    ):
        response = client.post("/internal/dispatch", json={"message_id": "m-err"})

    assert response.status_code == 503
    assert response.json()["detail"] == "RuntimeError"


def test_internal_dispatch_validates_request_body() -> None:
    """A missing message_id is rejected with 422."""
    response = client.post("/internal/dispatch", json={})
    assert response.status_code == 422


def test_internal_dispatch_bearer_enforced_when_token_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting the verify_bearer wiring on the route must fail this test."""
    monkeypatch.setenv("AGENTS_API_TOKEN", "sk-secret")
    get_settings.cache_clear()

    state = JobAppsState(message_id="m-auth")
    with patch(
        "services.job_apps.api.process_job_apps_message",
        new=AsyncMock(return_value=state),
    ):
        # A missing bearer returns 401.
        assert client.post("/internal/dispatch", json={"message_id": "m-auth"}).status_code == 401

        # A wrong bearer returns 401.
        headers = {"Authorization": "Bearer wrong"}
        response = client.post("/internal/dispatch", json={"message_id": "m-auth"}, headers=headers)
        assert response.status_code == 401

        # The correct bearer returns 204.
        headers = {"Authorization": "Bearer sk-secret"}
        response = client.post("/internal/dispatch", json={"message_id": "m-auth"}, headers=headers)
        assert response.status_code == 204
