"""Pins for the shared brief-title format.

The writers create pages with these exact titles and the fetcher
lookups match on them; both sides import the same helper, so these
tests pin the contract once.
"""

from datetime import date

from shared.brief_format import brief_title, short_date


def test_short_date_is_parenthesized_and_non_zero_padded() -> None:
    assert short_date(date(2026, 6, 4)) == "(6/4)"
    assert short_date(date(2026, 1, 1)) == "(1/1)"
    assert short_date(date(2026, 11, 30)) == "(11/30)"


def test_brief_title_format() -> None:
    assert brief_title("News Brief", date(2026, 6, 4)) == "News Brief (6/4)"
    assert brief_title("Morning Brief", date(2026, 12, 25)) == "Morning Brief (12/25)"


def test_january_first_does_not_substring_match_november() -> None:
    # The reason the format keeps its parentheses: a bare "1/1" would
    # substring-match "(11/1)" and cross-match Nov/Dec pages in January.
    assert short_date(date(2026, 1, 1)) not in brief_title("News Brief", date(2025, 11, 1))
