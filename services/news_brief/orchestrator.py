"""Wire the news-brief stages into a single callable.

run_news_brief() is the entry point for the FastAPI manual trigger and the
Modal cron: fetch, parse, curate, compose, write, audit. The curator's
single LLM call dominates the runtime; the end-to-end target is under one
minute on a typical 10-30 email day. Design notes: docs/design.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from services.news_brief.composer import compose
from services.news_brief.curator import curate
from services.news_brief.fetcher import fetch_news_emails
from services.news_brief.parser import parse_email_html
from services.news_brief.writer import write_brief
from shared.anthropic_cost import compute_cost_usd
from shared.db import NewsBriefRun, get_oneshot_session

if TYPE_CHECKING:
    from services.news_brief.types import CurateResult, ParsedEmail

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BriefResult:
    """Structured outcome from a single news-brief run."""

    page_id: str
    email_count: int
    story_count: int
    duration_s: float


def run_news_brief(*, since: datetime, until: datetime | None = None) -> str:
    """Run the full brief flow and return the new Notion page ID.

    The email window is [since, until); until=None means now (UTC). Raises
    on stage failure; zero-result stages are logged and still produce an
    empty-day page.
    """
    return run_with_metrics(since=since, until=until).page_id


def run_with_metrics(*, since: datetime, until: datetime | None = None) -> BriefResult:
    """Run the same flow as run_news_brief but expose counts and timing.

    Always writes a news_brief_runs audit row: with token and cost data on
    the happy path, metrics-only on the empty-emails path, and errored=True
    plus the failing stage name on stage failures.
    """
    window_until = until if until is not None else datetime.now(tz=UTC)
    brief_date = window_until.date()
    run_start = time.perf_counter()
    logger.info(
        "news_brief.start",
        extra={"since": since.isoformat(), "until": window_until.isoformat()},
    )

    # Each try covers exactly one stage so the news_brief.failed log line
    # can name which step blew up.
    try:
        fetched = fetch_news_emails(since=since, until=window_until)
    except Exception as exc:
        _emit_failure(stage="fetch", exc=exc)
        _audit_failure(brief_date=brief_date, stage="fetch", exc=exc, run_start=run_start)
        raise

    logger.info("news_brief.fetched", extra={"email_count": len(fetched)})

    parsed: list[ParsedEmail] = []
    try:
        for email in fetched:
            parsed.append(
                parse_email_html(
                    email.body_html,
                    message_id=email.message_id,
                    sender=email.sender_slug,
                    subject=email.subject,
                    received_at=email.received_at,
                )
            )
    except Exception as exc:
        _emit_failure(stage="parse", exc=exc)
        _audit_failure(
            brief_date=brief_date,
            stage="parse",
            exc=exc,
            run_start=run_start,
            emails_fetched=len(fetched),
        )
        raise

    logger.info("news_brief.parsed", extra={"parsed_count": len(parsed)})

    # Empty-day path: skip the curator (no LLM call, token columns stay
    # null) but still compose and write a page and persist an audit row so
    # the dashboard sees the run happened.
    curate_result: CurateResult | None = None
    if not parsed:
        logger.info("news_brief.no_emails")
    else:
        try:
            curate_result = curate(parsed)
        except Exception as exc:
            _emit_failure(stage="curate", exc=exc)
            _audit_failure(
                brief_date=brief_date,
                stage="curate",
                exc=exc,
                run_start=run_start,
                emails_fetched=len(fetched),
                stories_considered=len(parsed),
            )
            raise
        logger.info("news_brief.curated", extra={"story_count": len(curate_result.stories)})
        if not curate_result.stories:
            logger.info("news_brief.no_stories")

    stories = curate_result.stories if curate_result is not None else []

    try:
        markdown = compose(stories, parsed)
    except Exception as exc:
        _emit_failure(stage="compose", exc=exc)
        _audit_failure(
            brief_date=brief_date,
            stage="compose",
            exc=exc,
            run_start=run_start,
            emails_fetched=len(fetched),
            stories_considered=len(parsed) if parsed else None,
            curate_result=curate_result,
        )
        raise

    logger.info("news_brief.composed", extra={"markdown_chars": len(markdown)})

    try:
        page_id = write_brief(markdown_body=markdown, brief_date=brief_date)
    except Exception as exc:
        _emit_failure(stage="write", exc=exc)
        _audit_failure(
            brief_date=brief_date,
            stage="write",
            exc=exc,
            run_start=run_start,
            emails_fetched=len(fetched),
            stories_considered=len(parsed) if parsed else None,
            curate_result=curate_result,
        )
        raise

    duration_s = round(time.perf_counter() - run_start, 2)
    logger.info(
        "news_brief.done",
        extra={
            "page_id": page_id,
            "story_count": len(stories),
            "email_count": len(parsed),
            "duration_s": duration_s,
        },
    )

    _audit_success(
        brief_date=brief_date,
        emails_fetched=len(parsed),
        stories_considered=len(parsed),
        stories_included=len(stories),
        curate_result=curate_result,
        duration_s=duration_s,
        notion_page_id=page_id,
    )

    return BriefResult(
        page_id=page_id,
        email_count=len(parsed),
        story_count=len(stories),
        duration_s=duration_s,
    )


def _emit_failure(*, stage: str, exc: BaseException) -> None:
    """Log a news_brief.failed warning for the given stage.

    Fires before the audit row write so Modal logs capture the failure even
    if the audit write itself fails; the audit row is the authoritative
    record once the run completes.
    """
    logger.warning(
        "news_brief.failed",
        extra={"stage": stage, "error_msg": str(exc)[:200]},
    )


def _usage_columns(curate_result: CurateResult | None) -> dict[str, Any]:
    """Return token/cost audit columns, all null when no curator call was made."""
    if curate_result is None:
        return {
            "model": None,
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_tokens": None,
            "cost_usd": None,
        }
    usage = curate_result.usage
    return {
        "model": curate_result.model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_input_tokens or 0,
        "cost_usd": compute_cost_usd(curate_result.model, usage),
    }


def _audit_success(
    *,
    brief_date: date,
    emails_fetched: int,
    stories_considered: int,
    stories_included: int,
    curate_result: CurateResult | None,
    duration_s: float,
    notion_page_id: str,
) -> None:
    """Persist a successful run to news_brief_runs.

    Token/cost columns are populated only when a curator call was made; on
    the empty-emails path they stay null.
    """
    values: dict[str, Any] = {
        "brief_date": brief_date,
        "emails_fetched": emails_fetched,
        "stories_considered": stories_considered,
        "stories_included": stories_included,
        **_usage_columns(curate_result),
        "duration_s": Decimal(str(duration_s)),
        "notion_page_id": notion_page_id,
        "errored": False,
        "error_msg": None,
    }
    _upsert_audit_row(values)


def _audit_failure(
    *,
    brief_date: date,
    stage: str,
    exc: BaseException,
    run_start: float,
    emails_fetched: int = 0,
    stories_considered: int | None = None,
    curate_result: CurateResult | None = None,
) -> None:
    """Persist a failed run to news_brief_runs with errored=True.

    Records whatever metrics were available at the point of failure, such
    as the curator's usage when the failure happened after curation.
    """
    duration_s = round(time.perf_counter() - run_start, 2)
    values: dict[str, Any] = {
        "brief_date": brief_date,
        "emails_fetched": emails_fetched,
        "stories_considered": stories_considered,
        "stories_included": None,
        **_usage_columns(curate_result),
        "duration_s": Decimal(str(duration_s)),
        "notion_page_id": None,
        "errored": True,
        "error_msg": f"{stage}: {str(exc)[:500]}",
    }
    _upsert_audit_row(values)


def _upsert_audit_row(values: dict[str, Any]) -> None:
    """Run the UPSERT against news_brief_runs; never raises.

    Re-runs for the same brief_date overwrite the prior row's metrics (one
    row per day, enforced by the PK). Failures are logged at WARNING and
    swallowed so a flaky DB doesn't break the brief.
    """
    try:
        asyncio.run(_async_upsert_audit_row(values))
    except Exception:
        logger.warning(
            "news_brief.audit_failed",
            extra={"brief_date": values.get("brief_date")},
            exc_info=True,
        )


async def _async_upsert_audit_row(values: dict[str, Any]) -> None:
    update_cols = {k: v for k, v in values.items() if k != "brief_date"}
    stmt = (
        pg_insert(NewsBriefRun)
        .values(**values)
        .on_conflict_do_update(index_elements=["brief_date"], set_=update_cols)
    )
    async with get_oneshot_session() as session:
        await session.execute(stmt)
        await session.commit()
