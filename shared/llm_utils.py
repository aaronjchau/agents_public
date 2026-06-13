"""Mechanical scaffolding shared by every Anthropic call site.

truncate_body clips long email bodies to head plus tail; the
parse_stringified_* helpers defensively parse tool input that Anthropic
occasionally returns as a JSON string. Prompts and tool schemas stay
per service.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Truncation defaults shared by every call site: bodies over 8000 chars are
# clipped to first 6000 + marker + last 1000.
BODY_TRUNCATE_THRESHOLD = 8000
BODY_HEAD_KEEP = 6000
BODY_TAIL_KEEP = 1000
TRUNCATION_MARKER = "\n[... truncated ...]\n"

# Backslash not followed by a valid JSON string-escape character; strips
# the model's markdown-style escapes (like \$) from stringified tool
# input. See parse_stringified_tool_input.
_INVALID_JSON_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def truncate_body(
    body_text: str,
    *,
    threshold: int = BODY_TRUNCATE_THRESHOLD,
    head_keep: int = BODY_HEAD_KEEP,
    tail_keep: int = BODY_TAIL_KEEP,
) -> str:
    """Clip overlong bodies to head plus tail to keep prompt cost bounded.

    Long emails accumulate filler in the middle; the classification and
    extraction signal lives in the head and the footer (latest reply,
    signatures, unsubscribe hints). Bodies at or under threshold pass
    through unchanged.
    """
    if len(body_text) <= threshold:
        return body_text
    return body_text[:head_keep] + TRUNCATION_MARKER + body_text[-tail_keep:]


def _loads_lenient(raw: str, *, context: str, stop_reason: str | None) -> Any:
    """json.loads with invalid-escape recovery.

    Anthropic occasionally returns tool input as a stringified JSON object
    containing markdown-style escapes like \\$ that strict json.loads
    rejects. Try strict parsing first; on failure strip backslashes not
    followed by a valid JSON escape and retry. Dropping a markdown
    pre-escape is correctness-preserving for the prose fields it appears
    in.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        sanitized = _INVALID_JSON_ESCAPE.sub("", raw)
        try:
            return json.loads(sanitized)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{context} returned a non-JSON tool input string; "
                f"stop_reason={stop_reason!r} prefix={raw[:120]!r}"
            ) from exc


def parse_stringified_tool_input(
    raw: str,
    *,
    context: str,
    stop_reason: str | None = None,
) -> dict[str, Any]:
    """Defensively parse a JSON-stringified tool-input object.

    context names the calling flow so the error message identifies the
    failing call site.
    """
    result = _loads_lenient(raw, context=context, stop_reason=stop_reason)
    if not isinstance(result, dict):
        raise RuntimeError(
            f"{context}'s stringified tool input did not decode to an object; "
            f"stop_reason={stop_reason!r} got={type(result).__name__}"
        )
    return result


def parse_stringified_tool_list(
    raw: str,
    *,
    context: str,
    stop_reason: str | None = None,
) -> list[Any]:
    """Defensively parse a JSON-stringified array field of a tool input."""
    result = _loads_lenient(raw, context=context, stop_reason=stop_reason)
    if not isinstance(result, list):
        raise RuntimeError(
            f"{context}'s stringified tool input did not decode to a list; "
            f"stop_reason={stop_reason!r} got={type(result).__name__}"
        )
    return result
