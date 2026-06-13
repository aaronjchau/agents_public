"""Tests for the news-brief composer: heading order and format, em-dash
placement, dollar escaping, source-display mapping, and missing-link/email
fallbacks. All inputs are synthetic; the composer is pure code with no I/O.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from services.news_brief.composer import (
    CATEGORY_ORDER,
    EMPTY_BRIEF_MESSAGE,
    compose,
)
from services.news_brief.types import Link, ParsedEmail, StoryCandidate


def _email(
    *,
    message_id: str = "<msg-1@example.com>",
    sender: str = "theinformation",
    links: list[Link] | None = None,
) -> ParsedEmail:
    return ParsedEmail(
        message_id=message_id,
        sender=sender,
        subject="subject",
        received_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
        plain_text="body",
        links=links
        if links is not None
        else [Link(id=1, url="https://example.com/article", anchor_text="article")],
    )


def _story(
    *,
    category: str = "AI & Tech",
    label: str = "OpenAI",
    summary: str = "Released GPT-5.",
    source_email_id: str = "<msg-1@example.com>",
    link_id: int = 1,
) -> StoryCandidate:
    return StoryCandidate(
        category=category,  # type: ignore[arg-type]
        label=label,
        summary=summary,
        source_email_id=source_email_id,
        link_id=link_id,
    )


# ---------------------------------------------------------------------------
# Empty inputs
# ---------------------------------------------------------------------------


def test_empty_stories_returns_sentinel() -> None:
    assert compose([], []) == EMPTY_BRIEF_MESSAGE


def test_only_unresolvable_stories_returns_sentinel() -> None:
    """If every story references a missing email, no sections survive."""
    story = _story(source_email_id="<missing@example.com>")
    assert compose([story], []) == EMPTY_BRIEF_MESSAGE


# ---------------------------------------------------------------------------
# Heading format and category ordering
# ---------------------------------------------------------------------------


def test_single_story_renders_heading_and_quote_block() -> None:
    email = _email()
    story = _story()
    output = compose([story], [email])

    assert output.startswith('## AI & Tech {color="gray_bg"}\n')
    assert (
        "> **OpenAI:** Released GPT-5. — [The Information](https://example.com/article)" in output
    )


def test_categories_emitted_in_canonical_order() -> None:
    """Even if input order is shuffled, headings appear in the spec order."""
    email = _email()
    stories = [
        _story(category="Science & Research", label="CRISPR"),
        _story(category="AI & Tech", label="Anthropic"),
        _story(category="Markets", label="S&P"),
        _story(category="World & Politics", label="UN"),
        _story(category="Gadgets & Software", label="Vision Pro"),
        _story(category="Business & Economy", label="Earnings"),
    ]
    output = compose(stories, [email])

    indices = [output.index(f"## {category}") for category in CATEGORY_ORDER]
    assert indices == sorted(indices)


def test_empty_categories_omitted() -> None:
    email = _email()
    output = compose([_story(category="AI & Tech")], [email])

    assert "AI & Tech" in output
    for category in CATEGORY_ORDER:
        if category == "AI & Tech":
            continue
        assert category not in output


def test_heading_uses_gray_bg_color_attribute() -> None:
    email = _email()
    output = compose([_story()], [email])

    assert '{color="gray_bg"}' in output
    # Quote-block lines must not carry a color attribute.
    quote_lines = [line for line in output.splitlines() if line.startswith(">")]
    assert quote_lines
    for line in quote_lines:
        assert "{color=" not in line


def test_multiple_stories_in_same_category_share_one_heading() -> None:
    email = _email(
        links=[
            Link(id=1, url="https://example.com/a", anchor_text="a"),
            Link(id=2, url="https://example.com/b", anchor_text="b"),
        ]
    )
    stories = [
        _story(label="A", link_id=1),
        _story(label="B", link_id=2),
    ]
    output = compose(stories, [email])

    assert output.count('## AI & Tech {color="gray_bg"}') == 1
    assert "> **A:**" in output
    assert "> **B:**" in output


def test_sections_separated_by_blank_line() -> None:
    email = _email()
    stories = [
        _story(category="AI & Tech"),
        _story(category="Markets"),
    ]
    output = compose(stories, [email])
    # Two distinct sections joined by exactly one blank line between them.
    assert "\n\n## " in output


# ---------------------------------------------------------------------------
# Quote block / em-dash format
# ---------------------------------------------------------------------------


def test_em_dash_only_before_citation() -> None:
    """The em dash separator appears exactly once per story line."""
    email = _email()
    story = _story(summary="Release shipped on time.")
    output = compose([story], [email])

    quote_line = next(line for line in output.splitlines() if line.startswith(">"))
    assert quote_line.count("—") == 1
    # And it sits immediately before the citation.
    assert quote_line.endswith("[The Information](https://example.com/article)")
    assert " — [" in quote_line


# ---------------------------------------------------------------------------
# Dollar escaping (idempotent)
# ---------------------------------------------------------------------------


def test_unescaped_dollar_gets_escaped() -> None:
    email = _email()
    story = _story(summary="Raised $300B in funding.")
    output = compose([story], [email])

    assert "\\$300B" in output
    # The bare $ must not survive anywhere in the rendered output.
    assert "$" not in output.replace("\\$", "")


def test_already_escaped_dollar_left_alone() -> None:
    """Re-escaping must be idempotent."""
    email = _email()
    story = _story(summary="Raised \\$300B in funding.")
    output = compose([story], [email])

    assert "\\$300B" in output
    assert "\\\\$300B" not in output


def test_mixed_escaped_and_unescaped_dollars() -> None:
    email = _email()
    story = _story(summary="Up \\$100B from $50B last year.")
    output = compose([story], [email])

    assert "\\$100B" in output
    assert "\\$50B" in output
    assert "\\\\$" not in output


def test_dollar_in_label_is_escaped() -> None:
    email = _email()
    story = _story(label="$AAPL", summary="Stock moved.")
    output = compose([story], [email])

    assert "**\\$AAPL:**" in output


# ---------------------------------------------------------------------------
# Source display mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sender_slug", "expected"),
    [
        ("theinformation", "The Information"),
        ("nytimes", "NYT"),
        ("bloomberg", "Bloomberg"),
        ("theverge", "The Verge"),
        ("pragmaticengineer", "Pragmatic Engineer"),
        ("hackernewsletter", "Hacker Newsletter"),
    ],
)
def test_known_sender_slugs_map_to_display_name(sender_slug: str, expected: str) -> None:
    email = _email(sender=sender_slug)
    output = compose([_story()], [email])

    assert f"[{expected}](https://example.com/article)" in output


def test_unknown_sender_falls_through_to_titlecase() -> None:
    email = _email(sender="techcrunch")
    output = compose([_story()], [email])

    assert "[Techcrunch](https://example.com/article)" in output


def test_empty_sender_falls_back_to_unknown() -> None:
    email = _email(sender="")
    output = compose([_story()], [email])

    assert "[Unknown]" in output


# ---------------------------------------------------------------------------
# Link resolution / fallbacks
# ---------------------------------------------------------------------------


def test_link_resolved_by_id_when_available() -> None:
    email = _email(
        links=[
            Link(id=1, url="https://example.com/first", anchor_text="first"),
            Link(id=2, url="https://example.com/second", anchor_text="second"),
            Link(id=3, url="https://example.com/third", anchor_text="third"),
        ]
    )
    story = _story(link_id=2)
    output = compose([story], [email])

    assert "https://example.com/second" in output
    assert "https://example.com/first" not in output
    assert "https://example.com/third" not in output


def test_missing_link_id_falls_back_to_first_link() -> None:
    email = _email(
        links=[
            Link(id=1, url="https://example.com/first", anchor_text="first"),
            Link(id=2, url="https://example.com/second", anchor_text="second"),
        ]
    )
    story = _story(link_id=99)
    output = compose([story], [email])

    assert "https://example.com/first" in output
    assert "https://example.com/second" not in output


def test_email_with_no_links_renders_plain_text_source() -> None:
    """When the email has zero links, fall back to plain-text source name."""
    email = _email(links=[])
    story = _story(link_id=1)
    output = compose([story], [email])

    quote_line = next(line for line in output.splitlines() if line.startswith(">"))
    assert quote_line.endswith("— The Information")
    # No hyperlink form anywhere on the line.
    assert "](" not in quote_line


# ---------------------------------------------------------------------------
# Missing email handling
# ---------------------------------------------------------------------------


def test_unresolvable_email_id_skipped_silently(caplog: pytest.LogCaptureFixture) -> None:
    """Story whose source_email_id has no match is dropped, not raised."""
    email = _email(message_id="<msg-1@example.com>")
    good = _story(label="Good")
    orphan = _story(label="Orphan", source_email_id="<missing@example.com>")

    with caplog.at_level(logging.WARNING, logger="services.news_brief.composer"):
        output = compose([orphan, good], [email])

    assert "Good" in output
    assert "Orphan" not in output
    # The skip is logged so the orchestrator can decide whether to alert.
    assert any("missing@example.com" in record.message for record in caplog.records)


def test_empty_emails_with_stories_returns_sentinel() -> None:
    """No emails at all means nothing resolves; return the empty sentinel."""
    assert compose([_story()], []) == EMPTY_BRIEF_MESSAGE


# ---------------------------------------------------------------------------
# Multi-email integration
# ---------------------------------------------------------------------------


def test_stories_from_different_emails_resolve_independently() -> None:
    bloomberg = _email(
        message_id="<bloomberg-1@example.com>",
        sender="bloomberg",
        links=[Link(id=1, url="https://www.bloomberg.com/article-x", anchor_text="x")],
    )
    nyt = _email(
        message_id="<nyt-1@example.com>",
        sender="nytimes",
        links=[Link(id=1, url="https://nytimes.com/article-y", anchor_text="y")],
    )
    stories = [
        _story(
            category="Markets",
            label="Fed",
            source_email_id="<bloomberg-1@example.com>",
        ),
        _story(
            category="World & Politics",
            label="UN",
            source_email_id="<nyt-1@example.com>",
        ),
    ]
    output = compose(stories, [bloomberg, nyt])

    assert "[Bloomberg](https://www.bloomberg.com/article-x)" in output
    assert "[NYT](https://nytimes.com/article-y)" in output
