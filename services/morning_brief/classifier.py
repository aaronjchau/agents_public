"""Deterministic transforms of the brief's fetched data.

Buckets tasks, aggregates focus and LeetCode stats, and groups emails.
The judgment parts (intro, news curation) live in the LLM stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from services.morning_brief.constants import (
    EMAIL_CATEGORIES,
    FOCUS_WINDOW_DAYS,
    OTHER_PROJECT,
    PROJECTS,
    SCHOOL_PROJECT_IDS,
)

if TYPE_CHECKING:
    from datetime import date

    from services.morning_brief.constants import EmailCategory, Project
    from services.morning_brief.types import (
        EmailItem,
        FocusEntry,
        LeetCodeEntry,
        TaskRow,
    )

OVERDUE_RESCHEDULE_CUTOFF_DAYS = 14
WEEK_WINDOW_DAYS = 7
FOCUS_TABLE_DAYS = 5
RECENT_DAYS = 3


# --- classified outputs --------------------------------------------------


@dataclass(frozen=True)
class ClassifiedTask:
    """A task placed in a bucket, with the metadata the composer renders."""

    task: TaskRow
    project: Project
    due_today: bool = False
    ends_in_days: int | None = None  # Set for Today tasks with a multi-day block.
    percent_complete: int | None = None  # Set for Today tasks with a multi-day block.
    days_overdue: int | None = None  # Set for Overdue and Reschedule tasks.
    overdue_is_due: bool = False  # True means past Due date; False means elapsed Time Block.
    week_date: date | None = None  # The salient date for This Week tasks.


@dataclass(frozen=True)
class TaskSections:
    today: tuple[ClassifiedTask, ...] = ()
    this_week: tuple[ClassifiedTask, ...] = ()
    overdue: tuple[ClassifiedTask, ...] = ()
    reschedule: tuple[ClassifiedTask, ...] = ()


@dataclass(frozen=True)
class FocusDayRow:
    day: date
    minutes: int
    categories: tuple[str, ...]
    leetcode_count: int


@dataclass(frozen=True)
class FocusSummary:
    table: tuple[FocusDayRow, ...]  # Most recent day first.
    last3_minutes: int
    last3_daily_avg_min: float
    total14_minutes: int
    daily_avg14_min: float
    category_minutes_14d: tuple[tuple[str, int], ...]  # (category, minutes), descending.
    leetcode_count_7d: int
    leetcode_categories: tuple[str, ...]


@dataclass(frozen=True)
class EmailGroup:
    category: EmailCategory
    items: tuple[EmailItem, ...] = field(default_factory=tuple)


# --- task classification -------------------------------------------------


def classify_tasks(
    tasks: list[TaskRow],
    *,
    today: date,
    week_days: int = WEEK_WINDOW_DAYS,
) -> TaskSections:
    """Bucket tasks into Today / This Week / Overdue / Reschedule.

    School-project tasks are excluded from Overdue and Reschedule only
    (their classes have ended); they may still surface in Today/This Week.
    """
    window_end = today + timedelta(days=week_days)
    today_list: list[ClassifiedTask] = []
    week_list: list[ClassifiedTask] = []
    overdue_list: list[ClassifiedTask] = []
    reschedule_list: list[ClassifiedTask] = []

    for task in tasks:
        project = _resolve_project(task.project_ids)
        is_school = any(pid in SCHOOL_PROJECT_IDS for pid in task.project_ids)
        classified, bucket = _classify_one(task, project, today, window_end)
        if bucket == "today":
            today_list.append(classified)
        elif bucket == "week":
            week_list.append(classified)
        elif bucket == "overdue" and not is_school:
            overdue_list.append(classified)
        elif bucket == "reschedule" and not is_school:
            reschedule_list.append(classified)

    return TaskSections(
        today=tuple(sorted(today_list, key=lambda c: (not c.due_today, c.task.name.lower()))),
        this_week=tuple(
            sorted(week_list, key=lambda c: (c.week_date or today, c.task.name.lower()))
        ),
        overdue=tuple(
            sorted(
                overdue_list,
                key=lambda c: (not c.overdue_is_due, -(c.days_overdue or 0), c.task.name.lower()),
            )
        ),
        reschedule=tuple(
            sorted(reschedule_list, key=lambda c: (-(c.days_overdue or 0), c.task.name.lower()))
        ),
    )


def _classify_one(
    task: TaskRow,
    project: Project,
    today: date,
    window_end: date,
) -> tuple[ClassifiedTask, str | None]:
    due = task.due
    tb_start = task.time_block_start
    tb_end = task.time_block_end
    due_today = due == today

    # 1) Time Block includes today, or Due is today: Today.
    if due_today or _tb_includes_today(tb_start, tb_end, today):
        ends_in_days, percent = _multiday_progress(tb_start, tb_end, today)
        return (
            ClassifiedTask(
                task=task,
                project=project,
                due_today=due_today,
                ends_in_days=ends_in_days,
                percent_complete=percent,
            ),
            "today",
        )

    # 2) Past Due: Overdue or Reschedule.
    if due is not None and due < today:
        days = (today - due).days
        bucket = "overdue" if days <= OVERDUE_RESCHEDULE_CUTOFF_DAYS else "reschedule"
        return ClassifiedTask(
            task=task, project=project, days_overdue=days, overdue_is_due=True
        ), bucket

    # 3) Future Due within the week window: This Week.
    if due is not None and today < due <= window_end:
        return ClassifiedTask(task=task, project=project, week_date=due), "week"

    # 4) Time Block fully elapsed: Overdue or Reschedule.
    elapsed_end = tb_end if tb_end is not None else tb_start
    if elapsed_end is not None and elapsed_end < today:
        days = (today - elapsed_end).days
        bucket = "overdue" if days <= OVERDUE_RESCHEDULE_CUTOFF_DAYS else "reschedule"
        return (
            ClassifiedTask(task=task, project=project, days_overdue=days, overdue_is_due=False),
            bucket,
        )

    # 5) Time Block starts in the future within the window: This Week.
    if tb_start is not None and today < tb_start <= window_end:
        return ClassifiedTask(task=task, project=project, week_date=tb_start), "week"

    # Otherwise not surfaced (e.g. In Progress with no dates, or far out).
    return ClassifiedTask(task=task, project=project), None


def _tb_includes_today(tb_start: date | None, tb_end: date | None, today: date) -> bool:
    if tb_start is None:
        return False
    if tb_end is not None:
        return tb_start <= today <= tb_end
    return tb_start == today


def _multiday_progress(
    tb_start: date | None, tb_end: date | None, today: date
) -> tuple[int | None, int | None]:
    """For a multi-day block containing today, return (ends_in_days, percent).

    Single-day or absent blocks, or a block that does not contain today,
    return (None, None). The containment guard matters for due-today
    tasks: Due and Time Block are independent, so a task due today can
    carry a block entirely in the past or future, and without the guard
    those would yield out-of-range percentages and negative ends_in_days.
    """
    if tb_start is None or tb_end is None or tb_end <= tb_start:
        return None, None
    if not tb_start <= today <= tb_end:
        return None, None
    total_days = (tb_end - tb_start).days + 1
    elapsed_days = (today - tb_start).days + 1
    percent = round(100 * elapsed_days / total_days)
    ends_in_days = (tb_end - today).days
    return ends_in_days, percent


def _resolve_project(project_ids: tuple[str, ...]) -> Project:
    """Return the first recognized project relation, else the Other project."""
    for pid in project_ids:
        project = PROJECTS.get(pid)
        if project is not None:
            return project
    return OTHER_PROJECT


# --- focus aggregation ---------------------------------------------------


def summarize_focus(
    focus_entries: list[FocusEntry],
    leetcode: list[LeetCodeEntry],
    *,
    today: date,
) -> FocusSummary:
    """Aggregate focus and LeetCode entries into the Focus Hours summary.

    A multi-category entry counts its full minutes toward each of its
    categories, so the category_minutes_14d sum can exceed
    total14_minutes; day and window totals count each entry once.
    """
    minutes_by_day: dict[date, int] = {}
    categories_by_day: dict[date, set[str]] = {}
    category_minutes: dict[str, int] = {}
    for entry in focus_entries:
        minutes_by_day[entry.entry_date] = minutes_by_day.get(entry.entry_date, 0) + entry.minutes
        day_cats = categories_by_day.setdefault(entry.entry_date, set())
        for category in entry.categories:
            day_cats.add(category)
            category_minutes[category] = category_minutes.get(category, 0) + entry.minutes

    leetcode_by_day: dict[date, int] = {}
    leetcode_categories: set[str] = set()
    for problem in leetcode:
        if problem.last_submitted is not None:
            leetcode_by_day[problem.last_submitted] = (
                leetcode_by_day.get(problem.last_submitted, 0) + 1
            )
        leetcode_categories.update(problem.categories)

    table = tuple(
        FocusDayRow(
            day=(day := today - timedelta(days=offset)),
            minutes=minutes_by_day.get(day, 0),
            categories=tuple(sorted(categories_by_day.get(day, set()))),
            leetcode_count=leetcode_by_day.get(day, 0),
        )
        for offset in range(FOCUS_TABLE_DAYS)
    )

    last3 = sum(minutes_by_day.get(today - timedelta(days=i), 0) for i in range(RECENT_DAYS))
    total14 = sum(
        minutes_by_day.get(today - timedelta(days=i), 0) for i in range(FOCUS_WINDOW_DAYS)
    )

    return FocusSummary(
        table=table,
        last3_minutes=last3,
        last3_daily_avg_min=last3 / RECENT_DAYS,
        total14_minutes=total14,
        daily_avg14_min=total14 / FOCUS_WINDOW_DAYS,
        category_minutes_14d=tuple(
            sorted(category_minutes.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        leetcode_count_7d=len(leetcode),
        leetcode_categories=tuple(sorted(leetcode_categories)),
    )


# --- email grouping ------------------------------------------------------


def group_emails(emails: list[EmailItem]) -> list[EmailGroup]:
    """Order deduped emails into category groups (priority order)."""
    by_priority: dict[int, list[EmailItem]] = {}
    for email in emails:
        by_priority.setdefault(email.category_priority, []).append(email)
    groups: list[EmailGroup] = []
    for category in EMAIL_CATEGORIES:
        items = by_priority.get(category.priority)
        if items:
            groups.append(EmailGroup(category=category, items=tuple(items)))
    return groups
