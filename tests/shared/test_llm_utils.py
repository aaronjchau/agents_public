"""Tests for the shared LLM-call scaffolding (truncation + defensive parse)."""

from __future__ import annotations

import json

import pytest

from shared.llm_utils import (
    BODY_TRUNCATE_THRESHOLD,
    TRUNCATION_MARKER,
    parse_stringified_tool_input,
    parse_stringified_tool_list,
    truncate_body,
)

# ---------------------------------------------------------------------------
# truncate_body
# ---------------------------------------------------------------------------


def test_truncate_body_passes_short_bodies_through() -> None:
    body = "C" * 4000
    assert truncate_body(body) == body


def test_truncate_body_passes_exact_threshold_through() -> None:
    body = "C" * BODY_TRUNCATE_THRESHOLD
    assert truncate_body(body) == body


def test_truncate_body_clips_long_bodies_to_head_plus_tail() -> None:
    body = "A" * 6500 + "B" * 4000  # 10500 chars, over the 8000 threshold.
    out = truncate_body(body)
    assert out == "A" * 6000 + TRUNCATION_MARKER + "B" * 1000
    assert body not in out


def test_truncate_body_respects_custom_limits() -> None:
    body = "A" * 50 + "B" * 50
    out = truncate_body(body, threshold=60, head_keep=10, tail_keep=5)
    assert out == "A" * 10 + TRUNCATION_MARKER + "B" * 5
    # Below the custom threshold: unchanged.
    assert truncate_body(body, threshold=100) == body


# ---------------------------------------------------------------------------
# parse_stringified_tool_input
# ---------------------------------------------------------------------------


def test_parse_tool_input_strict_json_passes_through() -> None:
    payload = {"label": "Finance", "flagged": False}
    assert parse_stringified_tool_input(json.dumps(payload), context="t") == payload


def test_parse_tool_input_recovers_invalid_dollar_escape() -> None:
    raw = '{"summary": "Offer at \\$180k base."}'
    result = parse_stringified_tool_input(raw, context="t")
    # The \$ pre-escape is stripped to a bare $.
    assert result == {"summary": "Offer at $180k base."}


def test_parse_tool_input_preserves_valid_json_escapes() -> None:
    raw = '{"text": "line\\nbreak \\"quoted\\" \\\\$5"}'
    result = parse_stringified_tool_input(raw, context="t")
    assert result == {"text": 'line\nbreak "quoted" \\$5'}


def test_parse_tool_input_unparseable_raises_with_context() -> None:
    with pytest.raises(RuntimeError, match=r"curator returned a non-JSON tool input"):
        parse_stringified_tool_input(
            "this is definitely not json {{{", context="curator", stop_reason="max_tokens"
        )


def test_parse_tool_input_non_object_raises() -> None:
    with pytest.raises(RuntimeError, match="did not decode to an object"):
        parse_stringified_tool_input("[1, 2]", context="t")


# ---------------------------------------------------------------------------
# parse_stringified_tool_list
# ---------------------------------------------------------------------------


def test_parse_tool_list_strict_json_passes_through() -> None:
    assert parse_stringified_tool_list('[{"a": 1}]', context="t") == [{"a": 1}]


def test_parse_tool_list_recovers_invalid_dollar_escape() -> None:
    raw = '[{"summary": "Raised \\$40B at a \\$300B valuation."}]'
    result = parse_stringified_tool_list(raw, context="t")
    assert result == [{"summary": "Raised $40B at a $300B valuation."}]


def test_parse_tool_list_unparseable_raises_with_context() -> None:
    with pytest.raises(RuntimeError, match="t returned a non-JSON tool input"):
        parse_stringified_tool_list("not json [[[", context="t")


def test_parse_tool_list_non_list_raises() -> None:
    with pytest.raises(RuntimeError, match="did not decode to a list"):
        parse_stringified_tool_list('{"a": 1}', context="t")
