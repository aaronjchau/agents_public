"""Tests for the Morning Brief calendar fetcher."""

from datetime import date, datetime
from typing import Any
from unittest.mock import MagicMock

from services.morning_brief import constants
from services.morning_brief.calendar_fetcher import fetch_events, fetch_holidays

TODAY = date(2026, 6, 4)


def _service(items: list[dict[str, Any]]) -> MagicMock:
    service = MagicMock()
    list_call = MagicMock()
    list_call.execute.return_value = {"items": items}
    service.events.return_value.list.return_value = list_call
    return service


def _paged_service(*pages: dict[str, Any]) -> MagicMock:
    service = MagicMock()
    list_call = MagicMock()
    list_call.execute.side_effect = list(pages)
    service.events.return_value.list.return_value = list_call
    return service


def test_fetch_events_parses_timed_and_all_day() -> None:
    items = [
        {
            "summary": "Dentist",
            "start": {"dateTime": "2026-06-05T14:00:00-04:00"},
            "end": {"dateTime": "2026-06-05T15:00:00-04:00"},
        },
        {"summary": "Trip", "start": {"date": "2026-06-07"}, "end": {"date": "2026-06-09"}},
    ]
    service = _service(items)
    events = fetch_events(today=TODAY, service=service)

    assert len(events) == 2
    timed = events[0]
    assert timed.summary == "Dentist"
    assert timed.all_day is False
    assert timed.start == datetime.fromisoformat("2026-06-05T14:00:00-04:00")
    assert timed.end == datetime.fromisoformat("2026-06-05T15:00:00-04:00")

    all_day = events[1]
    assert all_day.summary == "Trip"
    assert all_day.all_day is True
    assert all_day.event_date == date(2026, 6, 7)
    assert all_day.start is None

    # Window covers [today, today+7] ET; query is to the primary calendar.
    kwargs = service.events.return_value.list.call_args.kwargs
    assert kwargs["calendarId"] == constants.PRIMARY_CALENDAR_ID
    assert kwargs["singleEvents"] is True
    assert kwargs["orderBy"] == "startTime"
    assert kwargs["timeMin"].startswith("2026-06-04T00:00:00")
    assert kwargs["timeMax"].startswith("2026-06-12T00:00:00")


def test_fetch_holidays_uses_holiday_calendar() -> None:
    service = _service([{"summary": "Juneteenth", "start": {"date": "2026-06-19"}}])
    holidays = fetch_holidays(today=TODAY, service=service)

    assert len(holidays) == 1
    assert holidays[0].summary == "Juneteenth"
    assert holidays[0].all_day is True
    kwargs = service.events.return_value.list.call_args.kwargs
    assert kwargs["calendarId"] == constants.US_HOLIDAYS_CALENDAR_ID


def test_fetch_events_missing_summary_defaults() -> None:
    service = _service([{"start": {"date": "2026-06-06"}}])
    events = fetch_events(today=TODAY, service=service)
    assert events[0].summary == "(no title)"


def test_fetch_events_empty() -> None:
    service = _service([])
    assert fetch_events(today=TODAY, service=service) == []


def test_fetch_events_follows_pagination() -> None:
    page1 = {
        "items": [{"summary": "A", "start": {"date": "2026-06-05"}}],
        "nextPageToken": "tok",
    }
    page2 = {"items": [{"summary": "B", "start": {"date": "2026-06-06"}}]}
    service = _paged_service(page1, page2)

    events = fetch_events(today=TODAY, service=service)

    # Both pages accumulated (no silent truncation at the page boundary).
    assert [e.summary for e in events] == ["A", "B"]
    assert service.events.return_value.list.return_value.execute.call_count == 2
    # The second call carried the page token.
    second_kwargs = service.events.return_value.list.call_args_list[1].kwargs
    assert second_kwargs["pageToken"] == "tok"
