"""Anthropic API cost calculator.

Hardcoded per-million-token prices (verified 2026-05-05) keyed by model,
multiplied against the five token buckets on an anthropic.types.Usage
object; returns USD as a Decimal for exact arithmetic. When
usage.cache_creation is null, the unsplit cache_creation_input_tokens
count is treated as a 5-minute write (the default TTL).
"""

from decimal import Decimal

from anthropic.types import Usage

_PER_MILLION = Decimal("1000000")


class _ModelPricing:
    """Per-million-token prices for one model family.

    Stored as Decimal so cost arithmetic matches the NUMERIC(10, 6)
    cost_usd column exactly.
    """

    __slots__ = ("cache_read", "cache_write_1h", "cache_write_5m", "input", "output")

    def __init__(
        self,
        input: str,
        output: str,
        cache_read: str,
        cache_write_5m: str,
        cache_write_1h: str,
    ) -> None:
        self.input = Decimal(input)
        self.output = Decimal(output)
        self.cache_read = Decimal(cache_read)
        self.cache_write_5m = Decimal(cache_write_5m)
        self.cache_write_1h = Decimal(cache_write_1h)


_SONNET = _ModelPricing(
    input="3",
    output="15",
    cache_read="0.30",
    cache_write_5m="3.75",
    cache_write_1h="6",
)
_OPUS = _ModelPricing(
    input="5",
    output="25",
    cache_read="0.50",
    cache_write_5m="6.25",
    cache_write_1h="10",
)
_HAIKU = _ModelPricing(
    input="1",
    output="5",
    cache_read="0.10",
    cache_write_5m="1.25",
    cache_write_1h="2",
)

PRICING: dict[str, _ModelPricing] = {
    "claude-sonnet-4-6": _SONNET,
    "claude-sonnet-4-5": _SONNET,
    "claude-sonnet-4": _SONNET,
    "claude-opus-4-7": _OPUS,
    "claude-opus-4-6": _OPUS,
    "claude-opus-4-5": _OPUS,
    "claude-haiku-4-5": _HAIKU,
}


def compute_cost_usd(model: str, usage: Usage) -> Decimal:
    """Return the dollar cost of one Anthropic response.

    model must be a key in PRICING; callers pass the exact model id from
    the API request. Unknown models raise ValueError so a typo at the
    call site fails loudly instead of silently writing $0 to the audit
    row. All-zero usage returns Decimal("0").
    """
    pricing = PRICING.get(model)
    if pricing is None:
        raise ValueError(f"unknown model {model!r}; add it to anthropic_cost.PRICING")

    cache_read = usage.cache_read_input_tokens or 0

    if usage.cache_creation is not None:
        cache_5m = usage.cache_creation.ephemeral_5m_input_tokens
        cache_1h = usage.cache_creation.ephemeral_1h_input_tokens
    else:
        cache_5m = usage.cache_creation_input_tokens or 0
        cache_1h = 0

    total = (
        Decimal(usage.input_tokens) * pricing.input
        + Decimal(usage.output_tokens) * pricing.output
        + Decimal(cache_read) * pricing.cache_read
        + Decimal(cache_5m) * pricing.cache_write_5m
        + Decimal(cache_1h) * pricing.cache_write_1h
    )
    return total / _PER_MILLION
