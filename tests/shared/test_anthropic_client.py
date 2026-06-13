"""Tests for shared.anthropic_client."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from shared.anthropic_client import get_anthropic_client
from shared.settings import get_settings


@pytest.fixture(autouse=True)
def _reset_settings() -> Any:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_returns_plain_client_when_tracing_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    with patch("shared.anthropic_client.wrap_anthropic") as mock_wrap:
        client = get_anthropic_client(api_key="sk-ant-xyz")

    mock_wrap.assert_not_called()
    assert hasattr(client, "messages")


def test_returns_plain_client_when_tracing_true_but_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    with patch("shared.anthropic_client.wrap_anthropic") as mock_wrap:
        get_anthropic_client(api_key="sk-ant-xyz")

    mock_wrap.assert_not_called()


def test_wraps_client_when_tracing_enabled_with_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")

    sentinel = object()
    with patch("shared.anthropic_client.wrap_anthropic", return_value=sentinel) as mock_wrap:
        result = get_anthropic_client(api_key="sk-ant-xyz")

    mock_wrap.assert_called_once()
    assert result is sentinel


def test_falls_back_to_settings_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-settings")
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)

    with patch("shared.anthropic_client.Anthropic") as mock_anthropic:
        get_anthropic_client()

    mock_anthropic.assert_called_once_with(api_key="sk-ant-from-settings")
