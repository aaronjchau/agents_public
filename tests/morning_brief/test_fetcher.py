"""Tests for the Morning Brief Notion-source fetchers."""

from datetime import date
from typing import Any
from unittest.mock import patch

from services.morning_brief import fetcher

TODAY = date(2026, 6, 4)


def _task_row() -> dict[str, Any]:
    return {
        "id": "task-1",
        "url": "https://notion.so/task-1",
        "properties": {
            "Task name": {"type": "title", "title": [{"plain_text": "Binary Search Set"}]},
            "Time Block": {"type": "date", "date": {"start": "2026-06-04", "end": "2026-06-08"}},
            "Due": {"type": "date", "date": {"start": "2026-06-04"}},
            "Project": {"type": "relation", "relation": [{"id": "proj-dsa"}]},
        },
    }


def test_fetch_tasks_parses_and_builds_filter() -> None:
    with patch("services.morning_brief.fetcher.query_all_pages", return_value=[_task_row()]) as q:
        tasks = fetcher.fetch_tasks(today=TODAY)

    assert len(tasks) == 1
    t = tasks[0]
    assert t.id == "task-1"
    assert t.url == "https://notion.so/task-1"
    assert t.name == "Binary Search Set"
    assert t.time_block_start == date(2026, 6, 4)
    assert t.time_block_end == date(2026, 6, 8)
    assert t.due == date(2026, 6, 4)
    assert t.project_ids == ("proj-dsa",)

    # Filter window is 60 days back from today.
    body = q.call_args.args[1]
    or_clause = body["filter"]["and"][3]["or"]
    assert {"property": "Time Block", "date": {"on_or_after": "2026-04-05"}} in or_clause
    assert body["filter"]["and"][2] == {"property": "Sub-tasks", "relation": {"is_empty": True}}


def test_fetch_focus_entries_parses_window() -> None:
    row = {
        "id": "f1",
        "properties": {
            "Date": {"type": "date", "date": {"start": "2026-06-03"}},
            "Minutes": {"type": "number", "number": 140},
            "Category": {
                "type": "multi_select",
                "multi_select": [{"name": "SWE"}, {"name": "DSA"}],
            },
        },
    }
    # A row with no Date is skipped.
    no_date = {"id": "f2", "properties": {"Minutes": {"type": "number", "number": 10}}}
    with patch("services.morning_brief.fetcher.query_all_pages", return_value=[row, no_date]) as q:
        entries = fetcher.fetch_focus_entries(today=TODAY)

    assert len(entries) == 1
    e = entries[0]
    assert e.entry_date == date(2026, 6, 3)
    assert e.minutes == 140
    assert e.categories == ("SWE", "DSA")
    # The 14-day inclusive window starts today minus 13 days.
    assert q.call_args.args[1]["filter"]["date"]["on_or_after"] == "2026-05-22"


def test_fetch_leetcode_excludes_placeholders() -> None:
    solved = {
        "id": "lc1",
        "properties": {
            "Problem": {"type": "title", "title": [{"plain_text": "Two Sum"}]},
            "Last Submitted": {"type": "date", "date": {"start": "2026-06-02"}},
            "Topics": {"type": "multi_select", "multi_select": [{"name": "Array"}]},
        },
    }
    placeholder = {
        "id": "lc0",
        "properties": {"Problem": {"type": "title", "title": [{"plain_text": "—"}]}},
    }
    with patch(
        "services.morning_brief.fetcher.query_all_pages", return_value=[solved, placeholder]
    ) as q:
        problems = fetcher.fetch_leetcode(today=TODAY)

    assert [p.problem for p in problems] == ["Two Sum"]
    assert problems[0].categories == ("Array",)
    assert problems[0].last_submitted == date(2026, 6, 2)
    # The 7-day inclusive window starts today minus 6 days.
    assert q.call_args.args[1]["filter"]["date"]["on_or_after"] == "2026-05-29"


def test_fetch_news_page_matches_by_date() -> None:
    rows = [
        {
            "id": "old",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "News 6/3"}]},
                "Date": {"type": "date", "date": {"start": "2026-06-03"}},
            },
        },
        {
            "id": "today",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "News 6/4"}]},
                "Date": {"type": "date", "date": {"start": "2026-06-04"}},
            },
        },
    ]
    with (
        patch("services.morning_brief.fetcher.query_all_pages", return_value=rows),
        patch(
            "services.morning_brief.fetcher.fetch_page_markdown", return_value="## AI & Tech"
        ) as md,
    ):
        page = fetcher.fetch_news_page(today=TODAY)

    assert page is not None
    assert page.page_id == "today"
    assert page.markdown == "## AI & Tech"
    md.assert_called_once_with("today")


def test_fetch_news_page_none_when_absent() -> None:
    rows = [
        {
            "id": "old",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "News 6/1"}]},
                "Date": {"type": "date", "date": {"start": "2026-06-01"}},
            },
        }
    ]
    with (
        patch("services.morning_brief.fetcher.query_all_pages", return_value=rows),
        patch("services.morning_brief.fetcher.fetch_page_markdown") as md,
    ):
        assert fetcher.fetch_news_page(today=TODAY) is None
        md.assert_not_called()


def test_fetch_news_page_title_fallback_requires_parentheses() -> None:
    """Jan 1 must not match a Nov 1 page: "(1/1)" is not in "(11/1)"."""
    rows = [
        {
            "id": "nov-page",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "News Brief (11/1)"}]},
                "Date": {"type": "date", "date": {"start": "2025-11-01"}},
            },
        }
    ]
    with (
        patch("services.morning_brief.fetcher.query_all_pages", return_value=rows),
        patch("services.morning_brief.fetcher.fetch_page_markdown") as md,
    ):
        assert fetcher.fetch_news_page(today=date(2026, 1, 1)) is None
        md.assert_not_called()


def test_fetch_news_page_matches_parenthesized_title_without_date() -> None:
    rows = [
        {
            "id": "today",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "News Brief (6/4)"}]},
            },
        }
    ]
    with (
        patch("services.morning_brief.fetcher.query_all_pages", return_value=rows),
        patch("services.morning_brief.fetcher.fetch_page_markdown", return_value="## AI & Tech"),
    ):
        page = fetcher.fetch_news_page(today=TODAY)

    assert page is not None
    assert page.page_id == "today"


def test_find_existing_brief_matches_parenthesized_short_date() -> None:
    rows = [
        {
            "id": "b1",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Morning Brief (6/4)"}]}
            },
        },
    ]
    with patch("services.morning_brief.fetcher.query_all_pages", return_value=rows):
        assert fetcher.find_existing_brief(today=TODAY) == "b1"


def test_find_existing_brief_none_when_no_match() -> None:
    rows = [
        {
            "id": "b1",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Morning Brief (6/3)"}]}
            },
        },
    ]
    with patch("services.morning_brief.fetcher.query_all_pages", return_value=rows) as q:
        assert fetcher.find_existing_brief(today=TODAY) is None
        # Existence query filters Type=Morning + created today, scanning
        # full pages (a 5-row slice could miss the marker on past-date runs).
        body = q.call_args.args[1]
        assert body["filter"]["and"][0] == {"property": "Type", "select": {"equals": "Morning"}}
        assert body["page_size"] == 100
        assert "max_pages" not in q.call_args.kwargs


def test_fetch_tasks_empty_result() -> None:
    with patch("services.morning_brief.fetcher.query_all_pages", return_value=[]):
        assert fetcher.fetch_tasks(today=TODAY) == []
