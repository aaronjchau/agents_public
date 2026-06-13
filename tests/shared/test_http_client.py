"""Tests for shared.http_client.dispatch_job_apps.

The dispatch client MUST be non-raising in every failure shape; the
parent webhook returns 200 even when Job Apps is down. Network
exception, 4xx, 5xx, and success paths are pinned.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from shared import http_client
from shared.settings import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Any:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ok_response() -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 204
    resp.text = ""
    return resp


def _error_response(status_code: int, body: str = "boom") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = body
    return resp


def _patch_async_client(post_mock: AsyncMock) -> Any:
    """Patch httpx.AsyncClient so async with yields a mock exposing post."""

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._post = post_mock

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> Any:
            return await self._post(*args, **kwargs)

    return patch("shared.http_client.httpx.AsyncClient", new=_FakeClient)


async def test_dispatch_job_apps_success_posts_to_internal_dispatch(
    monkeypatch: Any,
) -> None:
    """Happy path: POST to {job_apps_api_url}/internal/dispatch with bearer."""
    monkeypatch.setenv("AGENTS_API_TOKEN", "sk-dispatch-secret")
    post = AsyncMock(return_value=_ok_response())
    with _patch_async_client(post):
        await http_client.dispatch_job_apps("m-1")

    post.assert_awaited_once()
    call = post.await_args
    assert call is not None
    settings = get_settings()
    assert call.args == (f"{settings.job_apps_api_url}/internal/dispatch",)
    assert call.kwargs["json"] == {"message_id": "m-1"}
    assert call.kwargs["headers"]["Authorization"] == "Bearer sk-dispatch-secret"


async def test_dispatch_job_apps_omits_auth_header_when_token_unset(
    monkeypatch: Any,
) -> None:
    """With AGENTS_API_TOKEN unset no Authorization header is sent;
    a literal "Bearer None" would otherwise leak."""
    monkeypatch.delenv("AGENTS_API_TOKEN", raising=False)
    post = AsyncMock(return_value=_ok_response())
    with _patch_async_client(post):
        await http_client.dispatch_job_apps("m-1")

    post.assert_awaited_once()
    call = post.await_args
    assert call is not None
    assert "Authorization" not in call.kwargs["headers"]


async def test_dispatch_job_apps_non_2xx_does_not_raise(
    caplog: Any,
) -> None:
    """A 503 response logs a warning but never raises."""
    post = AsyncMock(return_value=_error_response(503, "graph timeout"))
    with _patch_async_client(post), caplog.at_level(logging.WARNING):
        # Should not raise.
        await http_client.dispatch_job_apps("m-non2xx")

    # The warning includes the status and message_id for triage, but not
    # the response body, which can echo email PII.
    assert any("dispatch_job_apps.non_2xx" in record.message for record in caplog.records)


async def test_dispatch_job_apps_swallows_network_errors(caplog: Any) -> None:
    """Any in-flight exception is logged and swallowed.

    Pub/Sub contract: the Triager webhook MUST return 200 even when
    Job Apps is unreachable. The client raising would break that.
    """
    post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    with _patch_async_client(post), caplog.at_level(logging.ERROR):
        # Should not raise.
        await http_client.dispatch_job_apps("m-down")

    assert any("dispatch_job_apps.failed" in record.message for record in caplog.records)
