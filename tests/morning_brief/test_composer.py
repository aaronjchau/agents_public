"""Tests for the Morning Brief composer (Notion enhanced-markdown render)."""

from datetime import date, datetime
from typing import Any

from services.morning_brief.classifier import (
    ClassifiedTask,
    EmailGroup,
    FocusDayRow,
    FocusSummary,
    TaskSections,
)
from services.morning_brief.composer import compose
from services.morning_brief.constants import EMAIL_CATEGORIES, PROJECTS
from services.morning_brief.types import CalendarEvent, EmailItem, NewsStory, TaskRow

TODAY = date(2026, 6, 4)
DSA = next(p for p in PROJECTS.values() if p.name == "DSA")


def _task(name: str, url: str = "https://notion.so/x") -> TaskRow:
    return TaskRow(
        id="i",
        url=url,
        name=name,
        time_block_start=None,
        time_block_end=None,
        due=None,
        project_ids=(),
    )


def _empty_focus() -> FocusSummary:
    table = tuple(FocusDayRow(TODAY, 0, (), 0) for _ in range(5))
    return FocusSummary(
        table=table,
        last3_minutes=0,
        last3_daily_avg_min=0.0,
        total14_minutes=0,
        daily_avg14_min=0.0,
        category_minutes_14d=(),
        leetcode_count_7d=0,
        leetcode_categories=(),
    )


def _compose(**overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "today": TODAY,
        "intro": "Good morning.",
        "focus": _empty_focus(),
        "task_sections": TaskSections(),
        "email_groups": [],
        "news": [],
    }
    kwargs.update(overrides)
    return compose(**kwargs)


def test_intro_and_dividers() -> None:
    out = _compose()
    assert out.startswith("Good morning.")
    assert "\n\n---\n\n" in out  # Sections are joined by dividers.


def test_intro_dollar_is_escaped() -> None:
    out = _compose(intro="You spent $40 today.")
    assert r"You spent \$40 today." in out


def test_focus_table_renders_with_header_and_hours() -> None:
    out = _compose()
    assert "### Focus Hours" in out
    assert '<table header-row="true">' in out
    assert "<td>**Day**</td>" in out
    assert "0.0h" in out  # Empty focus renders zero hours.


def test_today_callout_groups_by_project_with_color() -> None:
    today = (
        ClassifiedTask(task=_task("Binary Search"), project=DSA, due_today=True),
        ClassifiedTask(task=_task("Course"), project=DSA, ends_in_days=3, percent_complete=70),
    )
    out = _compose(task_sections=TaskSections(today=today))
    assert "### Today" in out
    assert "<callout>" in out
    assert '#### DSA {color="blue_bg"}' in out
    assert "**⚠️ DUE TODAY** — <mention-page url=" in out
    assert "Ends in 3 days **(70%)**" in out


def test_today_empty_shows_note() -> None:
    out = _compose()
    assert "Nothing scheduled today." in out


def test_overdue_distinguishes_due_vs_block() -> None:
    overdue = (
        ClassifiedTask(task=_task("PaidBill"), project=DSA, days_overdue=3, overdue_is_due=True),
        ClassifiedTask(task=_task("Blocked"), project=DSA, days_overdue=1, overdue_is_due=False),
    )
    out = _compose(task_sections=TaskSections(overdue=overdue))
    assert "### Overdue Tasks" in out
    assert "**⚠️ PAST DUE**" in out
    assert "Due 3 days ago" in out
    assert "Behind by 1 day" in out  # Singular form.


def test_reschedule_section() -> None:
    resched = (ClassifiedTask(task=_task("Ancient"), project=DSA, days_overdue=30),)
    out = _compose(task_sections=TaskSections(reschedule=resched))
    assert "### Tasks to Reschedule" in out
    assert "Behind by 30 days" in out


def test_emails_grouped_and_marketing_condensed() -> None:
    security = next(c for c in EMAIL_CATEGORIES if c.label == "Security")
    marketing = next(c for c in EMAIL_CATEGORIES if c.label == "Marketing")
    groups = [
        EmailGroup(security, (EmailItem("1", "Chase", "Payment", security.priority),)),
        EmailGroup(
            marketing,
            tuple(
                EmailItem(str(i), s, "promo", marketing.priority)
                for i, s in enumerate(["A", "B", "C", "D"])
            ),
        ),
    ]
    out = _compose(email_groups=groups)
    assert "#### 🔒 Security" in out
    assert "```plain text" in out
    assert "Chase — Payment" in out
    # Marketing condensed to a single summary line with "etc." (4 senders > 3).
    assert "4 newsletters/promos from A, B, C, etc." in out


def test_emails_empty_message() -> None:
    assert "No new emails." in _compose()


def test_email_subject_cannot_break_out_of_fence() -> None:
    # A crafted subject with backtick runs / newlines must not terminate the
    # ```plain text fence and inject markdown into the page.
    security = next(c for c in EMAIL_CATEGORIES if c.label == "Security")
    item = EmailItem("1", "Mallory", "x\n```\n# injected heading", security.priority)
    out = _compose(email_groups=[EmailGroup(security, (item,))])
    lines = out.splitlines()
    # Collapsed onto one neutralized line inside the fence...
    assert "x `` # injected heading" in out
    # ...so the only standalone ``` line is the legitimate fence closer and
    # the payload never lands at the start of a line as real markdown.
    assert lines.count("```") == 1
    assert not any(line.startswith("# injected") for line in lines)


def test_news_section_grouped_and_escaped() -> None:
    news = [
        NewsStory(
            "AI & Tech",
            "OpenAI",
            "Raised $40B at a high valuation.",
            "The Information",
            "https://x",
        ),
        NewsStory("Markets", "S&P", "Index up.", "Bloomberg", None),
    ]
    out = _compose(news=news)
    assert '#### AI & Tech {color="gray_bg"}' in out
    assert "> **OpenAI:**" in out
    assert r"\$40B" in out  # Dollar escaped.
    assert "[The Information](https://x)" in out
    # No URL renders the bare source name.
    assert "— Bloomberg" in out


def test_news_omitted_when_empty() -> None:
    assert "Major News" not in _compose(news=[])


def test_this_week_renders_events_and_holidays() -> None:
    events = [
        CalendarEvent("Dentist", datetime(2026, 6, 6, 14, 0), None, None, all_day=False),
    ]
    holidays = [CalendarEvent("Juneteenth", None, None, date(2026, 6, 19), all_day=True)]
    week = (ClassifiedTask(task=_task("Mock interview"), project=DSA, week_date=date(2026, 6, 7)),)
    out = _compose(task_sections=TaskSections(this_week=week), events=events, holidays=holidays)
    assert "### This Week" in out
    assert "Sunday" in out  # 6/7/2026 is a Sunday.
    assert "Dentist" in out
    assert "Juneteenth (holiday)" in out


def test_today_events_rendered_with_time() -> None:
    events = [CalendarEvent("Standup", datetime(2026, 6, 4, 9, 30), None, None, all_day=False)]
    out = _compose(events=events)
    assert "#### Calendar" in out
    assert "9:30 AM — Standup" in out
