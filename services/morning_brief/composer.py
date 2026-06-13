"""Render the Morning Brief body as Notion enhanced Markdown.

Assembles the sections from classified data plus the LLM-produced intro
and curated news; this module never calls an LLM. Dollar signs in rendered
text are escaped so Notion does not read them as inline math; email
sender/subject lines render inside code fences and are neutralized against
fence-terminating backtick runs instead.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from services.morning_brief.constants import (
    MARKETING_PRIORITY,
    NEWS_CATEGORY_ORDER,
    PROJECT_GROUP_ORDER,
)
from shared.brief_format import escape_dollar

if TYPE_CHECKING:
    from datetime import date, datetime

    from services.morning_brief.classifier import (
        ClassifiedTask,
        EmailGroup,
        FocusSummary,
        TaskSections,
    )
    from services.morning_brief.constants import Project
    from services.morning_brief.types import CalendarEvent, NewsStory, TaskRow

_SECTION_DIVIDER = "\n\n---\n\n"
# A run of 3+ backticks inside a ``` fence would terminate it early.
_FENCE_BREAK = re.compile(r"`{3,}")


def compose(
    *,
    today: date,
    intro: str,
    focus: FocusSummary,
    task_sections: TaskSections,
    email_groups: list[EmailGroup],
    news: list[NewsStory],
    events: list[CalendarEvent] | None = None,
    holidays: list[CalendarEvent] | None = None,
) -> str:
    """Assemble the full brief markdown body."""
    events = events or []
    holidays = holidays or []
    today_events = [e for e in events if _event_date(e) == today]
    future_events = [e for e in events if (d := _event_date(e)) is not None and d > today]
    future_holidays = [h for h in holidays if (d := _event_date(h)) is not None and d > today]

    sections = [
        escape_dollar(intro.strip()) or None,
        _focus_section(focus),
        _today_section(task_sections.today, today_events),
        _this_week_section(task_sections.this_week, future_events, future_holidays),
        _news_section(news),
        _emails_section(email_groups),
        _overdue_section(task_sections.overdue),
        _reschedule_section(task_sections.reschedule),
    ]
    return _SECTION_DIVIDER.join(section for section in sections if section)


# --- Focus Hours ---------------------------------------------------------


def _focus_section(focus: FocusSummary) -> str:
    paragraph = (
        f"Last 3 days: {format_hours(focus.last3_minutes)} logged "
        f"(~{format_hours(round(focus.last3_daily_avg_min))}/day) vs a 14-day average of "
        f"~{format_hours(round(focus.daily_avg14_min))}/day "
        f"({format_hours(focus.total14_minutes)} over 14 days). "
        f"LeetCode: {focus.leetcode_count_7d} problem(s) in the last 7 days"
    )
    if focus.leetcode_categories:
        paragraph += f" ({', '.join(focus.leetcode_categories)})."
    else:
        paragraph += "."
    top = [name for name, _ in focus.category_minutes_14d[:3]]
    if top:
        paragraph += f" Top focus areas: {', '.join(top)}."
    return f"### Focus Hours\n\n{paragraph}\n\n{_focus_table(focus)}"


def _focus_table(focus: FocusSummary) -> str:
    header = (
        '<table header-row="true">\n'
        "\t<tr>\n"
        "\t\t<td>**Day**</td>\n"
        "\t\t<td>**Focus Hours**</td>\n"
        "\t\t<td>**Categories**</td>\n"
        "\t\t<td>**LC Problems**</td>\n"
        "\t</tr>\n"
    )
    rows = "".join(
        "\t<tr>\n"
        f"\t\t<td>{_short_day(row.day)}</td>\n"
        f"\t\t<td>{format_hours(row.minutes)}</td>\n"
        f"\t\t<td>{', '.join(row.categories) if row.categories else '—'}</td>\n"
        f"\t\t<td>{row.leetcode_count}</td>\n"
        "\t</tr>\n"
        for row in focus.table
    )
    return f"{header}{rows}</table>"


# --- Today ---------------------------------------------------------------


def _today_section(
    today_tasks: tuple[ClassifiedTask, ...], today_events: list[CalendarEvent]
) -> str:
    lines: list[str] = []
    for project, items in _group_by_project(today_tasks):
        lines.append(f"\t{_h4(project)}")
        lines.extend(f"\t- {_today_bullet(ct)}" for ct in items)
    if today_events:
        lines.append("\t#### Calendar")
        lines.extend(f"\t- {_event_line(ev)}" for ev in today_events)
    if not lines:
        lines.append("\tNothing scheduled today.")
    return "### Today\n\n<callout>\n" + "\n".join(lines) + "\n</callout>"


def _today_bullet(ct: ClassifiedTask) -> str:
    if ct.due_today:
        return f"**⚠️ DUE TODAY** — {_mention(ct.task)}"
    if ct.ends_in_days is not None and ct.percent_complete is not None:
        return (
            f"{_mention(ct.task)} — Ends in {ct.ends_in_days} "
            f"{_pluralize(ct.ends_in_days, 'day')} **({ct.percent_complete}%)**"
        )
    return _mention(ct.task)


# --- This Week -----------------------------------------------------------


def _this_week_section(
    week_tasks: tuple[ClassifiedTask, ...],
    future_events: list[CalendarEvent],
    future_holidays: list[CalendarEvent],
) -> str | None:
    blocks: list[str] = []
    for project, items in _group_by_project(week_tasks):
        inner = [f"\t**{project.name}**"]
        for ct in items:
            suffix = f" — {_weekday(ct.week_date)}" if ct.week_date else ""
            inner.append(f"\t- {_mention(ct.task)}{suffix}")
        blocks.append(_callout("\n".join(inner), color=project.color))

    upcoming: list[str] = []
    for ev in future_events:
        day = _weekday(_event_date(ev))
        upcoming.append(f"\t- {day}: {escape_dollar(ev.summary)}")
    for holiday in future_holidays:
        day = _weekday(_event_date(holiday))
        upcoming.append(f"\t- {day}: {escape_dollar(holiday.summary)} (holiday)")
    if upcoming:
        blocks.append(_callout("\t**Upcoming**\n" + "\n".join(upcoming)))

    if not blocks:
        return None
    return "### This Week\n\n" + "\n".join(blocks)


# --- Major News ----------------------------------------------------------


def _news_section(news: list[NewsStory]) -> str | None:
    if not news:
        return None
    by_category: dict[str, list[NewsStory]] = {}
    for story in news:
        by_category.setdefault(story.category, []).append(story)
    ordered = [c for c in NEWS_CATEGORY_ORDER if c in by_category]
    ordered += [c for c in by_category if c not in NEWS_CATEGORY_ORDER]

    parts: list[str] = []
    for category in ordered:
        parts.append(f'#### {category} {{color="gray_bg"}}')
        parts.extend(_news_quote(story) for story in by_category[category])
    return "### Major News\n\n" + "\n".join(parts)


def _news_quote(story: NewsStory) -> str:
    source = escape_dollar(story.source)
    citation = f"[{source}]({story.url})" if story.url else source
    return f"> **{escape_dollar(story.label)}:** {escape_dollar(story.summary)} — {citation}"


# --- Emails --------------------------------------------------------------


def _emails_section(email_groups: list[EmailGroup]) -> str:
    if not email_groups:
        return "### Emails\n\nNo new emails."
    parts: list[str] = []
    for group in email_groups:
        parts.append(f"#### {group.category.emoji} {group.category.label}")
        if group.category.priority == MARKETING_PRIORITY:
            parts.append(_marketing_block(group))
        else:
            body = "\n".join(
                f"{_fence_safe(item.sender)} — {_fence_safe(item.subject)}" for item in group.items
            )
            parts.append(f"```plain text\n{body}\n```")
    return "### Emails\n\n" + "\n".join(parts)


def _marketing_block(group: EmailGroup) -> str:
    senders = list(dict.fromkeys(_fence_safe(item.sender) for item in group.items))
    shown = ", ".join(senders[:3])
    suffix = ", etc." if len(senders) > 3 else ""
    count = len(group.items)
    line = f"{count} newsletters/promos from {shown}{suffix}"
    return f"```plain text\n{line}\n```"


# --- Overdue / Reschedule ------------------------------------------------


def _overdue_section(overdue: tuple[ClassifiedTask, ...]) -> str | None:
    if not overdue:
        return None
    lines: list[str] = []
    for ct in overdue:
        days = ct.days_overdue or 0
        if ct.overdue_is_due:
            lines.append(
                f"- **⚠️ PAST DUE** — {_mention(ct.task)} — Due {days} {_pluralize(days, 'day')} ago"
            )
        else:
            lines.append(f"- {_mention(ct.task)} — Behind by {days} {_pluralize(days, 'day')}")
    return "### Overdue Tasks\n\n" + "\n".join(lines)


def _reschedule_section(reschedule: tuple[ClassifiedTask, ...]) -> str | None:
    if not reschedule:
        return None
    lines = [
        f"- {_mention(ct.task)} — Behind by {ct.days_overdue or 0} "
        f"{_pluralize(ct.days_overdue or 0, 'day')}"
        for ct in reschedule
    ]
    return "### Tasks to Reschedule\n\n" + "\n".join(lines)


# --- helpers -------------------------------------------------------------


def _group_by_project(
    items: tuple[ClassifiedTask, ...],
) -> list[tuple[Project, list[ClassifiedTask]]]:
    groups: dict[str, list[ClassifiedTask]] = {}
    project_by_name: dict[str, Project] = {}
    for ct in items:
        groups.setdefault(ct.project.name, []).append(ct)
        project_by_name[ct.project.name] = ct.project
    ordered_names = sorted(groups, key=lambda name: PROJECT_GROUP_ORDER.get(name, 99))
    return [(project_by_name[name], groups[name]) for name in ordered_names]


def _h4(project: Project) -> str:
    if project.color:
        return f'#### {project.name} {{color="{project.color}"}}'
    return f"#### {project.name}"


def _callout(body: str, *, color: str = "") -> str:
    open_tag = f'<callout color="{color}">' if color else "<callout>"
    return f"{open_tag}\n{body}\n</callout>"


def _mention(task: TaskRow) -> str:
    return f'<mention-page url="{task.url}">{escape_dollar(task.name)}</mention-page>'


def _event_line(event: CalendarEvent) -> str:
    if event.all_day or event.start is None:
        return escape_dollar(event.summary)
    return f"{_time(event.start)} — {escape_dollar(event.summary)}"


def _event_date(event: CalendarEvent) -> date | None:
    if event.start is not None:
        return event.start.date()
    return event.event_date


def format_hours(minutes: int) -> str:
    """Format minutes as hours, 90 to "1.5h"; shared with the LLM payload."""
    return f"{minutes / 60:.1f}h"


def _short_day(day: date) -> str:
    return f"{day.strftime('%a')} {day.month}/{day.day}"


def _weekday(day: date | None) -> str:
    return day.strftime("%A") if day is not None else ""


def _time(value: datetime) -> str:
    return value.strftime("%-I:%M %p")


def _pluralize(n: int, word: str) -> str:
    return word if n == 1 else f"{word}s"


def _fence_safe(text: str) -> str:
    """Neutralize untrusted text rendered inside a ``` fence.

    Collapses whitespace (a newline plus backticks could open a line that
    closes the fence) and breaks up 3+ backtick runs.
    """
    return _FENCE_BREAK.sub("``", " ".join(text.split()))
