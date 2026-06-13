"""Fetch Google Calendar events and US holidays for the Morning Brief.

If the shared refresh token lacks calendar.readonly scope (see
shared/gcal.py), every call here 403s; the orchestrator guards each fetch
and omits the calendar section, so a missing scope never breaks the brief.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from services.morning_brief import constants
from services.morning_brief.types import CalendarEvent
from shared.gcal import build_calendar_service

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

ET = ZoneInfo("America/New_York")
DEFAULT_WINDOW_DAYS = 7
_MAX_RESULTS = 250
_MAX_PAGES = 10


def fetch_events(
    *,
    today: date,
    days: int = DEFAULT_WINDOW_DAYS,
    service: Resource | None = None,
) -> list[CalendarEvent]:
    """Return primary-calendar events from start of today through today+days (ET)."""
    return _list_events(constants.PRIMARY_CALENDAR_ID, today=today, days=days, service=service)


def fetch_holidays(
    *,
    today: date,
    days: int = DEFAULT_WINDOW_DAYS,
    service: Resource | None = None,
) -> list[CalendarEvent]:
    """Return US holidays in the same [today, today+days] ET window."""
    return _list_events(constants.US_HOLIDAYS_CALENDAR_ID, today=today, days=days, service=service)


def _list_events(
    calendar_id: str,
    *,
    today: date,
    days: int,
    service: Resource | None,
) -> list[CalendarEvent]:
    api = service or build_calendar_service()
    time_min = datetime.combine(today, time.min, tzinfo=ET).isoformat()
    time_max = datetime.combine(today + timedelta(days=days + 1), time.min, tzinfo=ET).isoformat()

    events: list[CalendarEvent] = []
    page_token: str | None = None
    # singleEvents expands recurrences, so a busy window can exceed one page.
    # Follow nextPageToken (with a safety cap) rather than silently truncating.
    for _ in range(_MAX_PAGES):
        params: dict[str, Any] = {
            "calendarId": calendar_id,
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
            "timeZone": "America/New_York",
            "maxResults": _MAX_RESULTS,
        }
        if page_token:
            params["pageToken"] = page_token
        response = api.events().list(**params).execute()
        events.extend(_parse_event(item) for item in response.get("items") or [])
        next_token = response.get("nextPageToken")
        if not isinstance(next_token, str) or not next_token:
            break
        page_token = next_token
    return events


def _parse_event(item: dict[str, Any]) -> CalendarEvent:
    start = item.get("start") or {}
    end = item.get("end") or {}
    summary = item.get("summary") or "(no title)"
    start_dt = _parse_datetime(start.get("dateTime"))
    if start_dt is not None:
        return CalendarEvent(
            summary=summary,
            start=start_dt,
            end=_parse_datetime(end.get("dateTime")),
            event_date=None,
            all_day=False,
        )
    return CalendarEvent(
        summary=summary,
        start=None,
        end=None,
        event_date=_parse_date(start.get("date")),
        all_day=True,
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
