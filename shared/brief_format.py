"""Brief formatting shared by the brief services: titles and dollar escaping.

The create path (writers) and the find-existing path (fetcher lookups)
must agree on the exact title format, or re-runs duplicate pages
instead of overwriting. Both build on the parenthesized short date: a
bare "M/D" substring would let 1/1 match (11/1), cross-matching
Nov/Dec pages every early January.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date

# Match a single $ that is not already escaped. The lookbehind keeps the
# operation idempotent: re-running on already-escaped text is a no-op.
_UNESCAPED_DOLLAR = re.compile(r"(?<!\\)\$")


def escape_dollar(text: str) -> str:
    """Escape unescaped $ as \\$ so Notion doesn't read it as inline math."""
    return _UNESCAPED_DOLLAR.sub(r"\\$", text)


def short_date(d: date) -> str:
    """Parenthesized, non-zero-padded short date, e.g. (6/4).

    strftime("%-m/%-d") is glibc-only and breaks on Windows; this
    explicit form is portable.
    """
    return f"({d.month}/{d.day})"


def brief_title(prefix: str, d: date) -> str:
    """Page title for a dated brief, e.g. News Brief (6/4)."""
    return f"{prefix} {short_date(d)}"
