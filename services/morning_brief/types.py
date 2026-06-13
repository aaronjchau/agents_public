"""Domain models for the Morning Brief pipeline.

Raw inputs the fetchers produce and the classifier/composer consume.
Section/output types live with the classifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date, datetime


@dataclass(frozen=True)
class TaskRow:
    """A leaf task from the Tasks DB.

    time_block_end is None for single-day blocks. project_ids is the raw
    relation; the classifier maps it to a Project via constants.PROJECTS.
    """

    id: str
    url: str
    name: str
    time_block_start: date | None
    time_block_end: date | None
    due: date | None
    project_ids: tuple[str, ...]


@dataclass(frozen=True)
class FocusEntry:
    """One Focus Hours Log row (a logged focus session aggregate)."""

    entry_date: date
    minutes: int
    categories: tuple[str, ...]


@dataclass(frozen=True)
class LeetCodeEntry:
    """One solved LeetCode problem in the lookback window."""

    problem: str
    categories: tuple[str, ...]
    last_submitted: date | None


@dataclass(frozen=True)
class EmailItem:
    """A Gmail message surfaced in the Emails section.

    category_priority is the bucket it was deduped into (the lowest
    matching EmailCategory.priority).
    """

    message_id: str
    sender: str
    subject: str
    category_priority: int


@dataclass(frozen=True)
class CalendarEvent:
    """A Google Calendar event (primary calendar or US holidays).

    All-day events carry event_date with null start/end; timed events
    carry start/end.
    """

    summary: str
    start: datetime | None
    end: datetime | None
    event_date: date | None
    all_day: bool


@dataclass(frozen=True)
class NewsBriefPage:
    """Today's News Brief page: its id and rendered markdown body."""

    page_id: str
    markdown: str


@dataclass(frozen=True)
class NewsStory:
    """A curated Major-News story (LLM-selected from the News page).

    category is one of constants.NEWS_CATEGORY_ORDER (the brief groups by
    it). url is the source link, or None when unavailable.
    """

    category: str
    label: str
    summary: str
    source: str
    url: str | None
