"""Run the Morning Brief end to end and record the audit row.

Every fetch is guarded so a failing source drops its section, and the LLM
stage falls back to a deterministic intro; only the Notion write is fatal.
Design notes: docs/design.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert as pg_insert

from services.morning_brief import composer
from services.morning_brief.calendar_fetcher import fetch_events, fetch_holidays
from services.morning_brief.classifier import classify_tasks, group_emails, summarize_focus
from services.morning_brief.fetcher import (
    fetch_focus_entries,
    fetch_leetcode,
    fetch_news_page,
    fetch_tasks,
    find_existing_brief,
)
from services.morning_brief.gmail_fetcher import fetch_emails
from services.morning_brief.llm import BriefContent, write_brief_content
from services.morning_brief.writer import write_brief
from shared.anthropic_cost import compute_cost_usd
from shared.db import MorningBriefRun, get_oneshot_session

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from services.morning_brief.classifier import FocusSummary, TaskSections
    from services.morning_brief.types import (
        CalendarEvent,
        EmailItem,
        FocusEntry,
        LeetCodeEntry,
        NewsBriefPage,
        TaskRow,
    )

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class BriefResult:
    page_id: str
    tasks_today: int
    tasks_this_week: int
    tasks_overdue: int
    tasks_reschedule: int
    emails_count: int
    news_count: int
    duration_s: float


def run_morning_brief(*, today: date | None = None) -> str:
    """Run the brief and return the Notion page id."""
    return run_with_metrics(today=today).page_id


def run_with_metrics(*, today: date | None = None) -> BriefResult:
    start = time.perf_counter()
    brief_date = today or datetime.now(tz=ET).date()
    try:
        tasks: list[TaskRow] = _guard("tasks", lambda: fetch_tasks(today=brief_date), [])
        focus_entries: list[FocusEntry] = _guard(
            "focus", lambda: fetch_focus_entries(today=brief_date), []
        )
        leetcode: list[LeetCodeEntry] = _guard(
            "leetcode", lambda: fetch_leetcode(today=brief_date), []
        )
        news_page: NewsBriefPage | None = _guard(
            "news_page", lambda: fetch_news_page(today=brief_date), None
        )
        # Deliberately unguarded: swallowing a dedup-lookup failure would
        # take the create path and duplicate the page. The write is fatal
        # anyway, so a clean re-run is strictly better.
        existing: str | None = find_existing_brief(today=brief_date)
        emails: list[EmailItem] = _guard("emails", fetch_emails, [])
        events: list[CalendarEvent] = _guard("calendar", lambda: fetch_events(today=brief_date), [])
        holidays: list[CalendarEvent] = _guard(
            "holidays", lambda: fetch_holidays(today=brief_date), []
        )

        task_sections = classify_tasks(tasks, today=brief_date)
        focus = summarize_focus(focus_entries, leetcode, today=brief_date)
        email_groups = group_emails(emails)
        news_markdown = news_page.markdown if news_page else ""

        content = _llm_content(brief_date, focus, task_sections, news_markdown)

        markdown = composer.compose(
            today=brief_date,
            intro=content.intro,
            focus=focus,
            task_sections=task_sections,
            email_groups=email_groups,
            news=content.news,
            events=events,
            holidays=holidays,
        )
        page_id = write_brief(
            markdown_body=markdown,
            tldr=content.tldr,
            brief_date=brief_date,
            existing_page_id=existing,
        )
    except Exception as exc:
        duration = round(time.perf_counter() - start, 2)
        _upsert_audit_row(_failure_values(brief_date, exc, duration))
        raise

    duration = round(time.perf_counter() - start, 2)
    emails_count = sum(len(group.items) for group in email_groups)
    _upsert_audit_row(
        _success_values(brief_date, task_sections, emails_count, content, page_id, duration)
    )
    return BriefResult(
        page_id=page_id,
        tasks_today=len(task_sections.today),
        tasks_this_week=len(task_sections.this_week),
        tasks_overdue=len(task_sections.overdue),
        tasks_reschedule=len(task_sections.reschedule),
        emails_count=emails_count,
        news_count=len(content.news),
        duration_s=duration,
    )


def _guard(stage: str, thunk: Callable[[], Any], default: Any) -> Any:
    """Run a fetch stage; on any error log it and return default."""
    try:
        return thunk()
    except Exception:
        logger.warning("morning_brief.stage_failed", extra={"stage": stage}, exc_info=True)
        return default


def _llm_content(
    brief_date: date,
    focus: FocusSummary,
    task_sections: TaskSections,
    news_markdown: str,
) -> BriefContent:
    try:
        return write_brief_content(
            today=brief_date,
            focus=focus,
            task_sections=task_sections,
            news_markdown=news_markdown,
        )
    except Exception:
        logger.warning("morning_brief.llm_failed", exc_info=True)
        return _fallback_content(focus, task_sections)


def _fallback_content(focus: FocusSummary, task_sections: TaskSections) -> BriefContent:
    """Return a deterministic intro/TL;DR so the brief ships without the LLM."""
    hours = focus.total14_minutes / 60
    intro = (
        f"Good morning. You have {len(task_sections.today)} task(s) scheduled today and "
        f"{len(task_sections.overdue)} overdue. You've logged {hours:.1f}h of focus over the "
        "last 14 days."
    )
    tldr = f"{len(task_sections.today)} today, {len(task_sections.overdue)} overdue."
    return BriefContent(intro=intro, tldr=tldr, news=[], usage=None, model=None)


def _success_values(
    brief_date: date,
    task_sections: TaskSections,
    emails_count: int,
    content: BriefContent,
    page_id: str,
    duration: float,
) -> dict[str, Any]:
    usage = content.usage
    cost: Decimal | None = None
    if usage is not None and content.model is not None:
        try:
            cost = compute_cost_usd(content.model, usage)
        except ValueError:
            cost = None
    return {
        "brief_date": brief_date,
        "tasks_today": len(task_sections.today),
        "tasks_this_week": len(task_sections.this_week),
        "tasks_overdue": len(task_sections.overdue),
        "tasks_reschedule": len(task_sections.reschedule),
        "emails_count": emails_count,
        "news_stories": len(content.news),
        "model": content.model,
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None),
        "cost_usd": cost,
        "duration_s": Decimal(str(duration)),
        "notion_page_id": page_id,
        "errored": False,
        "error_msg": None,
    }


def _failure_values(brief_date: date, exc: Exception, duration: float) -> dict[str, Any]:
    return {
        "brief_date": brief_date,
        "duration_s": Decimal(str(duration)),
        "errored": True,
        "error_msg": f"{type(exc).__name__}: {exc}"[:500],
    }


def _upsert_audit_row(values: dict[str, Any]) -> None:
    """Upsert the audit row, keyed on brief_date; observational, never raises."""
    try:
        asyncio.run(_async_upsert_audit_row(values))
    except Exception:
        logger.warning(
            "morning_brief.audit_failed",
            extra={"brief_date": values.get("brief_date")},
            exc_info=True,
        )


async def _async_upsert_audit_row(values: dict[str, Any]) -> None:
    update_cols = {k: v for k, v in values.items() if k != "brief_date"}
    stmt = (
        pg_insert(MorningBriefRun)
        .values(**values)
        .on_conflict_do_update(index_elements=["brief_date"], set_=update_cols)
    )
    async with get_oneshot_session() as session:
        await session.execute(stmt)
        await session.commit()
