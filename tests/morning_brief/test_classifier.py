"""Tests for the Morning Brief classifier (task buckets, focus, emails)."""

from datetime import date, timedelta

from services.morning_brief.classifier import (
    ClassifiedTask,
    classify_tasks,
    group_emails,
    summarize_focus,
)
from services.morning_brief.types import EmailItem, FocusEntry, LeetCodeEntry, TaskRow

TODAY = date(2026, 6, 4)
DSA_ID = "proj-dsa"
CS106B_ID = "proj-cs106b"


def _task(
    name: str,
    *,
    tb_start: date | None = None,
    tb_end: date | None = None,
    due: date | None = None,
    projects: tuple[str, ...] = (DSA_ID,),
) -> TaskRow:
    return TaskRow(
        id=f"id-{name}",
        url=f"https://notion.so/{name}",
        name=name,
        time_block_start=tb_start,
        time_block_end=tb_end,
        due=due,
        project_ids=projects,
    )


def _only(section: tuple[ClassifiedTask, ...]) -> ClassifiedTask:
    assert len(section) == 1
    return section[0]


def test_time_block_range_including_today_is_today() -> None:
    t = _task("block-now", tb_start=TODAY - timedelta(days=1), tb_end=TODAY + timedelta(days=1))
    sections = classify_tasks([t], today=TODAY)
    assert _only(sections.today).task.name == "block-now"


def test_due_today_is_today_and_flagged() -> None:
    sections = classify_tasks([_task("due-now", due=TODAY)], today=TODAY)
    ct = _only(sections.today)
    assert ct.due_today is True


def test_multiday_block_progress() -> None:
    # Day 7 of a 10-day block (start 6 days ago, ends in 3) is 70%.
    t = _task("course", tb_start=TODAY - timedelta(days=6), tb_end=TODAY + timedelta(days=3))
    ct = _only(classify_tasks([t], today=TODAY).today)
    assert ct.percent_complete == 70
    assert ct.ends_in_days == 3


def test_single_day_block_today_has_no_progress() -> None:
    ct = _only(classify_tasks([_task("oneday", tb_start=TODAY)], today=TODAY).today)
    assert ct.percent_complete is None
    assert ct.ends_in_days is None


def test_due_today_with_noncontaining_block_has_no_progress() -> None:
    # Due today with a past Time Block lands in Today, but with no bogus
    # progress (would otherwise be e.g. percent=145, ends_in_days<0).
    past = _task(
        "due-with-past-block",
        due=TODAY,
        tb_start=TODAY - timedelta(days=15),
        tb_end=TODAY - timedelta(days=5),
    )
    ct = _only(classify_tasks([past], today=TODAY).today)
    assert ct.due_today is True
    assert ct.percent_complete is None
    assert ct.ends_in_days is None

    # A block starting in the future likewise yields no progress.
    future = _task(
        "due-with-future-block",
        due=TODAY,
        tb_start=TODAY + timedelta(days=6),
        tb_end=TODAY + timedelta(days=16),
    )
    ct2 = _only(classify_tasks([future], today=TODAY).today)
    assert ct2.percent_complete is None
    assert ct2.ends_in_days is None


def test_past_due_within_cutoff_is_overdue() -> None:
    ct = _only(classify_tasks([_task("od", due=TODAY - timedelta(days=5))], today=TODAY).overdue)
    assert ct.days_overdue == 5
    assert ct.overdue_is_due is True


def test_past_due_beyond_cutoff_is_reschedule() -> None:
    sections = classify_tasks([_task("stale", due=TODAY - timedelta(days=20))], today=TODAY)
    assert sections.overdue == ()
    assert _only(sections.reschedule).days_overdue == 20


def test_school_project_excluded_from_overdue() -> None:
    t = _task("cs", due=TODAY - timedelta(days=3), projects=(CS106B_ID,))
    sections = classify_tasks([t], today=TODAY)
    assert sections.overdue == ()
    assert sections.reschedule == ()


def test_future_due_within_window_is_this_week() -> None:
    ct = _only(
        classify_tasks([_task("soon", due=TODAY + timedelta(days=3))], today=TODAY).this_week
    )
    assert ct.week_date == TODAY + timedelta(days=3)


def test_elapsed_time_block_is_overdue_via_block() -> None:
    t = _task("late", tb_start=TODAY - timedelta(days=12), tb_end=TODAY - timedelta(days=10))
    ct = _only(classify_tasks([t], today=TODAY).overdue)
    assert ct.days_overdue == 10
    assert ct.overdue_is_due is False


def test_future_time_block_is_this_week() -> None:
    ct = _only(
        classify_tasks(
            [_task("upcoming", tb_start=TODAY + timedelta(days=5))], today=TODAY
        ).this_week
    )
    assert ct.week_date == TODAY + timedelta(days=5)


def test_no_dates_is_dropped() -> None:
    sections = classify_tasks([_task("floating", tb_start=None, due=None)], today=TODAY)
    assert sections.today == ()
    assert sections.this_week == ()
    assert sections.overdue == ()
    assert sections.reschedule == ()


def test_project_resolution() -> None:
    known = _only(classify_tasks([_task("k", due=TODAY)], today=TODAY).today)
    assert known.project.name == "DSA"
    unknown = _only(classify_tasks([_task("u", due=TODAY, projects=("nope",))], today=TODAY).today)
    assert unknown.project.name == "Other"


def test_today_sorts_due_today_first() -> None:
    block = _task("zzz-block", tb_start=TODAY)
    due = _task("aaa-due", due=TODAY)
    sections = classify_tasks([block, due], today=TODAY)
    # due-today sorts ahead of the (non-due) block despite alphabetical order.
    assert [c.task.name for c in sections.today] == ["aaa-due", "zzz-block"]


def test_summarize_focus_aggregates() -> None:
    entries = [
        FocusEntry(TODAY, minutes=60, categories=("SWE",)),
        FocusEntry(TODAY, minutes=30, categories=("DSA",)),
        FocusEntry(TODAY - timedelta(days=1), minutes=120, categories=("SWE",)),
        FocusEntry(TODAY - timedelta(days=10), minutes=45, categories=("Portfolio",)),
    ]
    leetcode = [
        LeetCodeEntry("Two Sum", ("Array",), TODAY),
        LeetCodeEntry("LRU Cache", ("Design",), TODAY - timedelta(days=2)),
    ]
    summary = summarize_focus(entries, leetcode, today=TODAY)

    # Table: 5 rows, most recent first.
    assert len(summary.table) == 5
    assert summary.table[0].day == TODAY
    assert summary.table[0].minutes == 90  # 60 + 30
    assert set(summary.table[0].categories) == {"SWE", "DSA"}
    assert summary.table[0].leetcode_count == 1
    assert summary.table[1].minutes == 120
    # Last-3-day total = 90 + 120 + 0.
    assert summary.last3_minutes == 210
    # 14-day total includes the day-10 entry.
    assert summary.total14_minutes == 255
    assert summary.leetcode_count_7d == 2
    assert summary.leetcode_categories == ("Array", "Design")
    # Category minutes sorted desc.
    assert summary.category_minutes_14d[0] == ("SWE", 180)


def test_summarize_focus_multi_category_counts_full_minutes_per_category() -> None:
    # "Time touching the category": a multi-category entry contributes its
    # full minutes to each category, so the per-category sum may exceed the
    # window total. Day/window totals still count the entry once.
    entries = [
        FocusEntry(TODAY, minutes=60, categories=("SWE", "DSA")),
        FocusEntry(TODAY, minutes=30, categories=("DSA",)),
    ]
    summary = summarize_focus(entries, [], today=TODAY)

    assert dict(summary.category_minutes_14d) == {"DSA": 90, "SWE": 60}
    assert summary.table[0].minutes == 90
    assert summary.total14_minutes == 90


def test_summarize_focus_empty() -> None:
    summary = summarize_focus([], [], today=TODAY)
    assert summary.total14_minutes == 0
    assert summary.last3_daily_avg_min == 0
    assert summary.leetcode_count_7d == 0
    assert all(row.minutes == 0 for row in summary.table)


def test_group_emails_orders_by_priority() -> None:
    emails = [
        EmailItem("m-news", "LinkedIn", "promo", category_priority=10),
        EmailItem("m-sec", "Google", "alert", category_priority=2),
        EmailItem("m-fin", "Chase", "statement", category_priority=3),
    ]
    groups = group_emails(emails)
    assert [g.category.priority for g in groups] == [2, 3, 10]
    assert groups[0].category.label == "Security"
    assert len(groups[0].items) == 1


def test_group_emails_empty() -> None:
    assert group_emails([]) == []
