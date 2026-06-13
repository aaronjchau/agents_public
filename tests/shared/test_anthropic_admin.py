"""Tests for shared.anthropic_admin."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared import anthropic_admin
from shared.anthropic_admin import (
    _aggregate_rows,
    _parse_bucket_date,
    sync_anthropic_spend,
)
from shared.settings import get_settings


@pytest.fixture(autouse=True)
def _reset_settings() -> Any:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --------------------------------------------------------- _parse_bucket_date


def test_parse_bucket_date_handles_z_suffix() -> None:
    assert _parse_bucket_date("2026-05-10T00:00:00Z") == date(2026, 5, 10)


def test_parse_bucket_date_handles_offset() -> None:
    assert _parse_bucket_date("2026-05-10T00:00:00+00:00") == date(2026, 5, 10)


def test_parse_bucket_date_returns_none_on_none() -> None:
    assert _parse_bucket_date(None) is None


def test_parse_bucket_date_returns_none_on_garbage() -> None:
    assert _parse_bucket_date("not-a-date") is None


# ----------------------------------------------------------- _aggregate_rows


def test_aggregate_joins_cost_and_usage_by_date_and_model() -> None:
    # Anthropic's cost_report returns amount in cents; see anthropic_admin._CENTS_PER_USD.
    cost_rows = [
        {"starting_at": "2026-05-10T00:00:00Z", "model": "claude-opus-4-7", "amount": "125"},
        {"starting_at": "2026-05-10T00:00:00Z", "model": "claude-sonnet-4-6", "amount": "50"},
    ]
    usage_rows = [
        {
            "starting_at": "2026-05-10T00:00:00Z",
            "model": "claude-opus-4-7",
            "uncached_input_tokens": 10_000,
            "output_tokens": 2_000,
            "cache_read_input_tokens": 5_000,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 500,
                "ephemeral_1h_input_tokens": 100,
            },
        },
    ]

    agg = _aggregate_rows(cost_rows, usage_rows)

    opus = agg[(date(2026, 5, 10), "claude-opus-4-7")]
    assert opus["cost_usd"] == Decimal("1.25")
    assert opus["input_tokens"] == 10_000
    assert opus["output_tokens"] == 2_000
    assert opus["cache_read_tokens"] == 5_000
    assert opus["cache_write_5m"] == 500
    assert opus["cache_write_1h"] == 100

    sonnet = agg[(date(2026, 5, 10), "claude-sonnet-4-6")]
    assert sonnet["cost_usd"] == Decimal("0.50")
    assert sonnet["input_tokens"] == 0  # no usage row, zeros are fine


def test_aggregate_sums_multiple_rows_per_key() -> None:
    """Anthropic returns multiple rows per bucket when split by workspace/api_key."""
    cost_rows = [
        {"starting_at": "2026-05-10T00:00:00Z", "model": "m", "amount": "10"},
        {"starting_at": "2026-05-10T00:00:00Z", "model": "m", "amount": "15"},
    ]
    agg = _aggregate_rows(cost_rows, [])
    assert agg[(date(2026, 5, 10), "m")]["cost_usd"] == Decimal("0.25")


def test_aggregate_drops_rows_with_unparseable_date() -> None:
    cost_rows = [{"starting_at": "garbage", "model": "m", "amount": "1.0"}]
    assert _aggregate_rows(cost_rows, []) == {}


def test_aggregate_drops_cost_row_with_missing_amount() -> None:
    cost_rows = [{"starting_at": "2026-05-10T00:00:00Z", "model": "m", "amount": None}]
    assert _aggregate_rows(cost_rows, []) == {}


# ------------------------------------------------------ sync_anthropic_spend


async def test_sync_no_ops_without_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_ADMIN_API_KEY", raising=False)
    get_settings.cache_clear()

    with patch.object(anthropic_admin, "fetch_cost_report") as cost:
        n = await sync_anthropic_spend(since=date(2026, 5, 1), until=date(2026, 5, 1))

    assert n == 0
    cost.assert_not_called()


async def test_sync_upserts_rows_when_admin_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "sk-ant-admin01-test")
    get_settings.cache_clear()

    cost_rows = [
        {"starting_at": "2026-05-10T00:00:00Z", "model": "claude-opus-4-7", "amount": "1.50"},
    ]
    usage_rows = [
        {
            "starting_at": "2026-05-10T00:00:00Z",
            "model": "claude-opus-4-7",
            "uncached_input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 25,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 10,
                "ephemeral_1h_input_tokens": 5,
            },
        },
    ]

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    @asynccontextmanager
    async def fake_get_session() -> Any:
        yield session

    with (
        patch.object(anthropic_admin, "fetch_cost_report", return_value=cost_rows),
        patch.object(anthropic_admin, "fetch_usage_report", return_value=usage_rows),
        patch.object(anthropic_admin, "get_session", fake_get_session),
    ):
        n = await sync_anthropic_spend(since=date(2026, 5, 10), until=date(2026, 5, 10))

    assert n == 1
    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()


async def test_sync_returns_zero_when_api_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "sk-ant-admin01-test")
    get_settings.cache_clear()

    with (
        patch.object(anthropic_admin, "fetch_cost_report", return_value=[]),
        patch.object(anthropic_admin, "fetch_usage_report", return_value=[]),
        patch.object(anthropic_admin, "get_session") as gs,
    ):
        n = await sync_anthropic_spend(since=date(2026, 5, 10), until=date(2026, 5, 10))

    assert n == 0
    gs.assert_not_called()


async def test_sync_passes_inclusive_date_window_to_admin_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API treats ending_at as exclusive; sync_anthropic_spend's
    until is inclusive, so the call adds one day to the end."""
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "sk-ant-admin01-test")
    get_settings.cache_clear()

    fetch_cost = MagicMock(return_value=[])
    fetch_usage = MagicMock(return_value=[])

    with (
        patch.object(anthropic_admin, "fetch_cost_report", fetch_cost),
        patch.object(anthropic_admin, "fetch_usage_report", fetch_usage),
    ):
        await sync_anthropic_spend(since=date(2026, 5, 10), until=date(2026, 5, 12))

    # ending_at is until plus one day (exclusive boundary).
    kwargs = fetch_cost.call_args.kwargs
    assert kwargs["starting_at"] == datetime(2026, 5, 10, 0, 0, tzinfo=UTC)
    assert kwargs["ending_at"] == datetime(2026, 5, 13, 0, 0, tzinfo=UTC)
