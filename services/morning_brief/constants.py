"""Pinned IDs and mappings for the Morning Brief.

Workspace-specific values come from env (shared/settings.py); the rest is
pinned here, not fetched at runtime, so the brief never burns API calls
rediscovering it.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.settings import get_settings

_settings = get_settings()

# --- Notion data sources -------------------------------------------------

TASKS_DATA_SOURCE_ID = _settings.mb_tasks_data_source_id
FOCUS_HOURS_DATA_SOURCE_ID = _settings.mb_focus_hours_data_source_id
LEETCODE_DATA_SOURCE_ID = _settings.mb_leetcode_data_source_id
BRIEFS_HUB_DATA_SOURCE_ID = _settings.mb_briefs_hub_data_source_id
NEWS_DATA_SOURCE_ID = _settings.notion_news_data_source_id


# --- Projects ------------------------------------------------------------


@dataclass(frozen=True)
class Project:
    """A Tasks-DB project: display name plus Notion background-color token.

    An empty color means no highlight; Inbox and any unrecognized project
    relation render under a plain heading.
    """

    name: str
    color: str


# Project page id to (display name, background color), sourced from the
# MB_PROJECTS env value: {"<page id>": ["Name", "color"]}.
PROJECTS: dict[str, Project] = {
    pid: Project(spec[0], spec[1] if len(spec) > 1 else "")
    for pid, spec in _settings.mb_projects.items()
    if spec
}

# Heading shown for tasks whose project relation matches none of the above.
OTHER_PROJECT = Project("Other", "")

# Render order for project groups: MB_PROJECTS insertion order, then Other.
PROJECT_GROUP_ORDER: dict[str, int] = {
    project.name: i for i, project in enumerate((*PROJECTS.values(), OTHER_PROJECT))
}

# Projects whose courses have ended: their overdue tasks are stale noise,
# excluded from Overdue / Reschedule. Sourced from MB_SCHOOL_PROJECT_IDS.
SCHOOL_PROJECT_IDS: frozenset[str] = frozenset(_settings.mb_school_project_ids)


# --- Email categories ----------------------------------------------------


@dataclass(frozen=True)
class EmailCategory:
    """One Gmail label bucket for the Emails section.

    query is the Gmail search (a newer_than:1d window is appended at fetch
    time). priority orders the sections and resolves multi-label dedup: an
    email is shown once, under its numerically smallest matching category.
    """

    query: str
    label: str
    emoji: str
    priority: int


# Hardcoded; do NOT fetch labels at runtime. The News label is
# intentionally absent: news is sourced from the News DB.
EMAIL_CATEGORIES: tuple[EmailCategory, ...] = (
    EmailCategory("label:⚠️-flagged", "Flagged", "⚠️", 1),
    EmailCategory("label:security", "Security", "🔒", 2),
    EmailCategory("label:finance", "Finance", "💰", 3),
    EmailCategory("label:people", "People", "🧍🏻", 4),
    EmailCategory("label:medical", "Medical", "🏥", 5),
    EmailCategory("label:purchases", "Purchases", "📦", 6),
    EmailCategory("label:returns", "Returns", "🔙", 7),
    EmailCategory("label:home", "Home", "🏠", 8),
    EmailCategory("label:notifications", "Notifications", "❗", 9),
    EmailCategory("label:marketing", "Marketing", "📰", 10),
)

# Marketing is condensed to a single summary line; every other category
# lists emails individually.
MARKETING_PRIORITY = next(c.priority for c in EMAIL_CATEGORIES if c.label == "Marketing")


# --- Windows ---------------------------------------------------------------

# Focus Hours lookback, shared by the fetch window and the aggregation.
FOCUS_WINDOW_DAYS = 14


# --- Calendar ------------------------------------------------------------

PRIMARY_CALENDAR_ID = _settings.mb_primary_calendar_id
US_HOLIDAYS_CALENDAR_ID = "en.usa#holiday@group.v.calendar.google.com"


# --- News ----------------------------------------------------------------

# Category heading order, mirroring the News Brief page so Major News
# groups read consistently with the source.
NEWS_CATEGORY_ORDER: tuple[str, ...] = (
    "AI & Tech",
    "World & Politics",
    "Business & Economy",
    "Markets",
    "Gadgets & Software",
    "Science & Research",
)

# Page metadata for the Briefs Hub.
BRIEF_ICON = "☀️"
BRIEF_TYPE = "Morning"
