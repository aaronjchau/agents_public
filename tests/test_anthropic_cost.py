"""Tests for shared.anthropic_cost.compute_cost_usd.

Expected costs are computed by hand from tokens * rate / 1_000_000 so a
typo in the pricing table fails the test instead of silently misbilling
future audit rows.
"""

from decimal import Decimal

import pytest
from anthropic.types import Usage
from anthropic.types.cache_creation import CacheCreation

from shared.anthropic_cost import compute_cost_usd


def _usage(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
    cache_creation: CacheCreation | None = None,
) -> Usage:
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_creation=cache_creation,
    )


def test_sonnet_simple_input_output() -> None:
    """Sonnet at $3/M input + $15/M output. 10k in + 1k out = 0.03 + 0.015 = 0.045."""
    usage = _usage(input_tokens=10_000, output_tokens=1_000)
    assert compute_cost_usd("claude-sonnet-4-6", usage) == Decimal("0.045")


def test_sonnet_4_5_uses_same_pricing() -> None:
    usage = _usage(input_tokens=10_000, output_tokens=1_000)
    assert compute_cost_usd("claude-sonnet-4-5", usage) == Decimal("0.045")


def test_opus_simple_input_output() -> None:
    """Opus at $5/M input + $25/M output. 10k in + 1k out = 0.05 + 0.025 = 0.075."""
    usage = _usage(input_tokens=10_000, output_tokens=1_000)
    assert compute_cost_usd("claude-opus-4-7", usage) == Decimal("0.075")


def test_haiku_simple_input_output() -> None:
    """Haiku at $1/M input + $5/M output. 10k in + 1k out = 0.01 + 0.005 = 0.015."""
    usage = _usage(input_tokens=10_000, output_tokens=1_000)
    assert compute_cost_usd("claude-haiku-4-5", usage) == Decimal("0.015")


def test_cache_read_uses_cache_read_rate() -> None:
    """Sonnet cache reads at $0.30/M. 100k cache_read = 0.030."""
    usage = _usage(input_tokens=0, output_tokens=0, cache_read_input_tokens=100_000)
    assert compute_cost_usd("claude-sonnet-4-6", usage) == Decimal("0.030000")


def test_cache_creation_breakdown_5m_and_1h() -> None:
    """When cache_creation is set, 5m and 1h tokens use their respective rates."""
    cache = CacheCreation(
        ephemeral_5m_input_tokens=10_000,
        ephemeral_1h_input_tokens=20_000,
    )
    usage = _usage(input_tokens=0, output_tokens=0, cache_creation=cache)
    # Sonnet: 10k * $3.75/M + 20k * $6/M = 0.0375 + 0.12 = 0.1575
    assert compute_cost_usd("claude-sonnet-4-6", usage) == Decimal("0.1575")


def test_cache_creation_input_tokens_fallback_treated_as_5m() -> None:
    """An unsplit cache_creation_input_tokens count bills as a 5-minute write."""
    usage = _usage(
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=10_000,
    )
    # Sonnet 5m write: 10k * $3.75/M = 0.0375
    assert compute_cost_usd("claude-sonnet-4-6", usage) == Decimal("0.0375")


def test_all_zero_usage_returns_zero() -> None:
    usage = _usage()
    assert compute_cost_usd("claude-sonnet-4-6", usage) == Decimal("0")


def test_unknown_model_raises() -> None:
    usage = _usage(input_tokens=1, output_tokens=1)
    with pytest.raises(ValueError, match="unknown model"):
        compute_cost_usd("gpt-5-not-real", usage)


def test_cost_combines_all_buckets_for_opus() -> None:
    """All five token buckets sum into one Opus cost."""
    cache = CacheCreation(
        ephemeral_5m_input_tokens=5_000,
        ephemeral_1h_input_tokens=7_000,
    )
    usage = _usage(
        input_tokens=1_000,
        output_tokens=500,
        cache_read_input_tokens=2_000,
        cache_creation=cache,
    )
    # 1k*5 + 500*25 + 2k*0.50 + 5k*6.25 + 7k*10 = 119,750 / 1M = 0.11975.
    assert compute_cost_usd("claude-opus-4-7", usage) == Decimal("0.11975")
