"""Fetch the Morning Brief's Notion-sourced inputs.

Every function takes today (an ET date computed by the orchestrator) so
the windows are timezone-correct. Parsing is defensive: a renamed Notion
property yields an empty section rather than a crash.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from services.morning_brief import constants, notion_parse
from services.morning_brief.notion_reader import (
    fetch_page_markdown,
    query_all_pages,
)
from services.morning_brief.types import (
    FocusEntry,
    LeetCodeEntry,
    NewsBriefPage,
    TaskRow,
)
from shared.brief_format import short_date

if TYPE_CHECKING:
    from datetime import date

TASKS_LOOKBACK_DAYS = 60
LEETCODE_WINDOW_DAYS = 7

# The LeetCode log uses an em-dash title for placeholder (unsolved) rows.
_PLACEHOLDER_TITLES = frozenset({"", "—"})


def fetch_tasks(*, today: date) -> list[TaskRow]:
    """Fetch active, scheduled, and recently-overdue leaf tasks.

    Filters to leaf tasks (no sub-tasks) that are not Done/Cancelled and
    are either In Progress / Scheduled or time-blocked / due within the
    last 60 days, so stale-but-recent overdue items still surface.
    """
    sixty_days_ago = (today - timedelta(days=TASKS_LOOKBACK_DAYS)).isoformat()
    body = {
        "filter": {
            "and": [
                {"property": "Status", "status": {"does_not_equal": "Done"}},
                {"property": "Status", "status": {"does_not_equal": "Cancelled"}},
                {"property": "Sub-tasks", "relation": {"is_empty": True}},
                {
                    "or": [
                        {"property": "Status", "status": {"equals": "In Progress"}},
                        {"property": "Status", "status": {"equals": "Scheduled"}},
                        {"property": "Time Block", "date": {"on_or_after": sixty_days_ago}},
                        {"property": "Due", "date": {"on_or_after": sixty_days_ago}},
                    ]
                },
            ]
        },
        "page_size": 100,
    }
    rows = query_all_pages(constants.TASKS_DATA_SOURCE_ID, body)
    return [_parse_task(r) for r in rows]


def fetch_focus_entries(*, today: date) -> list[FocusEntry]:
    """Fetch Focus Hours Log rows in the trailing 14-day window (inclusive)."""
    start = (today - timedelta(days=constants.FOCUS_WINDOW_DAYS - 1)).isoformat()
    body = {"filter": {"property": "Date", "date": {"on_or_after": start}}, "page_size": 100}
    rows = query_all_pages(constants.FOCUS_HOURS_DATA_SOURCE_ID, body)
    out: list[FocusEntry] = []
    for row in rows:
        props = row.get("properties") or {}
        entry_date = notion_parse.date_start(props, "Date")
        if entry_date is None:
            continue
        minutes = notion_parse.number(props, "Minutes") or 0
        out.append(
            FocusEntry(
                entry_date=entry_date,
                minutes=int(minutes),
                categories=notion_parse.multi_select(props, "Category"),
            )
        )
    return out


def fetch_leetcode(*, today: date) -> list[LeetCodeEntry]:
    """Fetch solved LeetCode problems from the trailing 7-day window.

    Excludes placeholder rows (empty or em-dash titles). Categories come
    from the first multi_select property, name-agnostically.
    """
    start = (today - timedelta(days=LEETCODE_WINDOW_DAYS - 1)).isoformat()
    body = {
        "filter": {"property": "Last Submitted", "date": {"on_or_after": start}},
        "page_size": 50,
    }
    rows = query_all_pages(constants.LEETCODE_DATA_SOURCE_ID, body)
    out: list[LeetCodeEntry] = []
    for row in rows:
        problem = notion_parse.title_text(row).strip()
        if problem in _PLACEHOLDER_TITLES:
            continue
        props = row.get("properties") or {}
        out.append(
            LeetCodeEntry(
                problem=problem,
                categories=notion_parse.first_multi_select(row),
                last_submitted=notion_parse.date_start(props, "Last Submitted"),
            )
        )
    return out


def fetch_news_page(*, today: date) -> NewsBriefPage | None:
    """Return today's News Brief page (id plus rendered markdown), or None.

    Matches on the page's Date property equalling today, falling back to
    the parenthesized short date "(M/D)" in the title. The fallback must
    keep the parentheses: a bare "M/D" substring lets 1/1 match 11/1, so a
    failed news cron could pull a stale Nov/Dec page every early January.
    """
    body = {"sorts": [{"timestamp": "created_time", "direction": "descending"}], "page_size": 5}
    rows = query_all_pages(constants.NEWS_DATA_SOURCE_ID, body, max_pages=1)
    marker = short_date(today)
    for row in rows:
        props = row.get("properties") or {}
        if notion_parse.date_start(props, "Date") == today or marker in notion_parse.title_text(
            row
        ):
            page_id = row.get("id")
            if isinstance(page_id, str):
                return NewsBriefPage(page_id=page_id, markdown=fetch_page_markdown(page_id))
    return None


def find_existing_brief(*, today: date) -> str | None:
    """Return the page id of today's Morning brief if it exists, else None.

    Filters the Briefs Hub to Type=Morning created today, then confirms
    the parenthesized short date "(M/D)" in the title so re-runs overwrite
    instead of duplicating.
    """
    # Full pagination at page_size 100: with a past-date override the
    # created_time window matches every brief since, and a short slice
    # could miss the marker page and cause a duplicate on re-run.
    body = {
        "filter": {
            "and": [
                {"property": "Type", "select": {"equals": constants.BRIEF_TYPE}},
                {"timestamp": "created_time", "created_time": {"on_or_after": today.isoformat()}},
            ]
        },
        "page_size": 100,
    }
    rows = query_all_pages(constants.BRIEFS_HUB_DATA_SOURCE_ID, body)
    marker = short_date(today)
    for row in rows:
        if marker in notion_parse.title_text(row):
            page_id = row.get("id")
            if isinstance(page_id, str):
                return page_id
    return None


def _parse_task(row: dict[str, Any]) -> TaskRow:
    props = row.get("properties") or {}
    time_block_start, time_block_end = notion_parse.date_range(props, "Time Block")
    return TaskRow(
        id=row.get("id") or "",
        url=row.get("url") or "",
        name=notion_parse.title_text(row),
        time_block_start=time_block_start,
        time_block_end=time_block_end,
        due=notion_parse.date_start(props, "Due"),
        project_ids=notion_parse.relation_ids(props, "Project"),
    )
