"""Render StoryCandidate lists into Notion-flavored markdown.

compose() groups stories by category, emits one gray-background heading per
non-empty category in canonical order, and renders each story as a one-line
quote block with a hyperlinked source citation when a URL is recoverable.

Escape rule: dollar signs render as \\$ because Notion otherwise treats
$...$ as inline math. Re-escaping is idempotent: \\$ stays \\$, bare $
becomes \\$. The em dash in a story line appears only before the source
citation; the curator is prompted to keep em dashes out of summaries, but
this module does not police that.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shared.brief_format import escape_dollar

if TYPE_CHECKING:
    from services.news_brief.types import Category, Link, ParsedEmail, StoryCandidate

logger = logging.getLogger(__name__)

# Canonical heading order. compose() emits them in this sequence and skips
# any category with zero stories for the day.
CATEGORY_ORDER: tuple[Category, ...] = (
    "AI & Tech",
    "World & Politics",
    "Business & Economy",
    "Markets",
    "Gadgets & Software",
    "Science & Research",
)

# Maps sender slug to the human-readable source name shown in the citation;
# unknown senders fall through to a title-cased slug.
_SOURCE_DISPLAY: dict[str, str] = {
    "theinformation": "The Information",
    "nytimes": "NYT",
    "bloomberg": "Bloomberg",
    "theverge": "The Verge",
    "pragmaticengineer": "Pragmatic Engineer",
    "hackernewsletter": "Hacker Newsletter",
}

EMPTY_BRIEF_MESSAGE = "No newsworthy stories today."


def compose(stories: list[StoryCandidate], emails: list[ParsedEmail]) -> str:
    """Render today's stories into Notion-flavored markdown.

    Citations resolve source_email_id and link_id against emails. Order
    within a category is preserved; empty or fully unresolvable input
    returns EMPTY_BRIEF_MESSAGE so the writer always has page content.
    """
    if not stories:
        return EMPTY_BRIEF_MESSAGE

    emails_by_id = {email.message_id: email for email in emails}

    grouped: dict[Category, list[str]] = {category: [] for category in CATEGORY_ORDER}
    for story in stories:
        email = emails_by_id.get(story.source_email_id)
        if email is None:
            logger.warning(
                "skipping story with unknown source_email_id=%r (label=%r)",
                story.source_email_id,
                story.label,
            )
            continue
        grouped[story.category].append(_render_story_line(story, email))

    sections: list[str] = []
    for category in CATEGORY_ORDER:
        lines = grouped[category]
        if not lines:
            continue
        heading = f'## {category} {{color="gray_bg"}}'
        sections.append("\n".join([heading, *lines]))

    if not sections:
        return EMPTY_BRIEF_MESSAGE

    return "\n\n".join(sections)


def _render_story_line(story: StoryCandidate, email: ParsedEmail) -> str:
    """Render one story as a single-line quote block."""
    label = escape_dollar(story.label)
    summary = escape_dollar(story.summary)
    citation = _render_citation(story, email)
    return f"> **{label}:** {summary} — {citation}"


def _render_citation(story: StoryCandidate, email: ParsedEmail) -> str:
    """Pick the citation form: linked when a URL is recoverable, plain otherwise.

    Falls back from the matching link_id to the email's first link to a
    plain-text source name, so a single missing link can't tank an entire
    day's brief.
    """
    source_display = _display_name(email.sender)
    link = _resolve_link(story.link_id, email.links)
    if link is None:
        return source_display
    return f"[{source_display}]({link.url})"


def _resolve_link(link_id: int, links: list[Link]) -> Link | None:
    """Return the link with id == link_id, else the first link, else None."""
    for link in links:
        if link.id == link_id:
            return link
    return links[0] if links else None


def _display_name(sender_slug: str) -> str:
    """Map a sender slug to its display name; unknown slugs fall back to title case."""
    if sender_slug in _SOURCE_DISPLAY:
        return _SOURCE_DISPLAY[sender_slug]
    return sender_slug.title() if sender_slug else "Unknown"
