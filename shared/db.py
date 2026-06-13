"""Async SQLAlchemy engine, session factory, and ORM models.

One lazily created engine per process; sessions use expire_on_commit=False
so attributes stay usable after commit.

Design notes: docs/design.md.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import func

from shared.settings import get_settings


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all ORM models."""


class GmailWatchState(Base):
    """Per-account Gmail Pub/Sub watch state.

    One row per watched mailbox. current_history_id is the cursor the
    webhook resumes history.list from when a push notification arrives.
    """

    __tablename__ = "gmail_watch_state"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    current_history_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_renewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TriagerRun(Base):
    """Audit row for one Triager classification and label-apply run.

    Doubles as the idempotency table: the webhook short-circuits when
    message_id already exists. Also feeds the dashboard's Triager queue.
    """

    __tablename__ = "triager_runs"
    # Mirrors the migration-created indexes so autogenerate never drops them.
    __table_args__ = (
        Index("ix_triager_runs_classified_at", "classified_at"),
        Index("idx_triager_runs_email_received_at_desc", text("email_received_at DESC")),
    )

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    classified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    email_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # NULL when the run failed before a label was applied; error holds
    # the reason and the row replays via /classify-email.
    primary_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    flagged: Mapped[bool] = mapped_column(nullable=False, default=False)
    subject: Mapped[str | None] = mapped_column(String(1000))
    sender: Mapped[str | None] = mapped_column(String(500))
    reasoning: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    # Same concept as anthropic_spend's cache_write_*; renaming would cost a migration.
    cache_creation_5m: Mapped[int | None] = mapped_column(Integer)
    cache_creation_1h: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(precision=10, scale=6))
    stage_timings_ms: Mapped[dict[str, int] | None] = mapped_column(JSONB, nullable=True)


class JobAppsRun(Base):
    """Audit row for one Job Apps pipeline run.

    Doubles as the idempotency table for the Triager dispatch and records
    the path the graph took: sublabel, match outcome, Notion write, draft,
    and terminal reason. The dashboard reads it for the review queue and
    pending drafts.
    """

    __tablename__ = "job_apps_runs"
    __table_args__ = (
        Index("ix_job_apps_runs_classified_at", "classified_at"),
        Index("idx_job_apps_runs_email_received_at_desc", text("email_received_at DESC")),
    )

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    classified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    email_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sublabel: Mapped[str | None] = mapped_column(String(32))
    match_status: Mapped[str | None] = mapped_column(String(16))
    notion_row_id: Mapped[str | None] = mapped_column(String(64))
    status_changed: Mapped[bool] = mapped_column(nullable=False, default=False)
    new_status: Mapped[str | None] = mapped_column(String(32))
    draft_id: Mapped[str | None] = mapped_column(String(64))
    terminal_reason: Mapped[str | None] = mapped_column(String(64))
    errored: Mapped[bool] = mapped_column(nullable=False, default=False)
    error_msg: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    # Same concept as anthropic_spend's cache_write_*; renaming would cost a migration.
    cache_creation_5m: Mapped[int | None] = mapped_column(Integer)
    cache_creation_1h: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(precision=10, scale=6))
    node_timings_ms: Mapped[dict[str, int] | None] = mapped_column(JSONB, nullable=True)


class NewsBriefRun(Base):
    """Audit row for one News Brief curation run.

    One row per brief_date; re-running the same day upserts.
    """

    __tablename__ = "news_brief_runs"
    __table_args__ = (Index("news_brief_runs_ran_at_idx", text("ran_at DESC")),)

    brief_date: Mapped[date] = mapped_column(Date, primary_key=True)
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    emails_fetched: Mapped[int] = mapped_column(Integer, nullable=False)
    stories_considered: Mapped[int | None] = mapped_column(Integer)
    stories_included: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(precision=10, scale=6))
    duration_s: Mapped[Decimal | None] = mapped_column(Numeric(precision=8, scale=2))
    notion_page_id: Mapped[str | None] = mapped_column(Text)
    errored: Mapped[bool] = mapped_column(nullable=False, default=False)
    error_msg: Mapped[str | None] = mapped_column(Text)


class MorningBriefRun(Base):
    """Audit row for one Morning Brief composition run.

    One row per brief_date; re-running the same day upserts. Counts are
    nullable so a failure before classification still writes a row
    recording the error.
    """

    __tablename__ = "morning_brief_runs"
    __table_args__ = (Index("morning_brief_runs_ran_at_idx", text("ran_at DESC")),)

    brief_date: Mapped[date] = mapped_column(Date, primary_key=True)
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    tasks_today: Mapped[int | None] = mapped_column(Integer)
    tasks_this_week: Mapped[int | None] = mapped_column(Integer)
    tasks_overdue: Mapped[int | None] = mapped_column(Integer)
    tasks_reschedule: Mapped[int | None] = mapped_column(Integer)
    emails_count: Mapped[int | None] = mapped_column(Integer)
    news_stories: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(precision=10, scale=6))
    duration_s: Mapped[Decimal | None] = mapped_column(Numeric(precision=8, scale=2))
    notion_page_id: Mapped[str | None] = mapped_column(Text)
    errored: Mapped[bool] = mapped_column(nullable=False, default=False)
    error_msg: Mapped[str | None] = mapped_column(Text)


class AnthropicSpend(Base):
    """Daily Anthropic billing pulled from the Admin Cost and Usage Report APIs.

    One row per (spend_date, model); the daily cron upserts on that key,
    so re-runs replace prior rows. cost_usd is the authoritative billed
    amount; the token fields keep the usage breakdown so the dashboard
    can show variance against the per-call estimates.
    """

    __tablename__ = "anthropic_spend"
    __table_args__ = (Index("anthropic_spend_date_idx", text("spend_date DESC")),)

    spend_date: Mapped[date] = mapped_column(Date, primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(precision=10, scale=6), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_read_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_write_5m: Mapped[int | None] = mapped_column(BigInteger)
    cache_write_1h: Mapped[int | None] = mapped_column(BigInteger)
    pulled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def _translate_url(raw: str) -> tuple[URL, dict[str, Any]]:
    """Translate a generic Postgres URL into SQLAlchemy-asyncpg form.

    Plain postgresql:// is upgraded to postgresql+asyncpg:// (SQLAlchemy
    otherwise defaults to psycopg2, which is not installed), and Neon's
    sslmode query param, which asyncpg rejects, becomes the equivalent
    connect_args SSL setting. One connection string therefore serves
    .env, Vercel, and the Modal Secret unchanged.
    """
    url = make_url(raw)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+asyncpg")
    connect_args: dict[str, Any] = {}

    sslmode = url.query.get("sslmode")
    if sslmode is not None:
        url = url.set(query={k: v for k, v in url.query.items() if k != "sslmode"})
        if sslmode != "disable":
            # asyncpg applies default cert verification when ssl=True.
            connect_args["ssl"] = True

    return url, connect_args


@lru_cache
def get_engine() -> AsyncEngine:
    url, connect_args = _translate_url(get_settings().database_url)
    return create_async_engine(
        url,
        pool_pre_ping=True,
        echo=False,
        connect_args=connect_args,
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


@asynccontextmanager
async def get_oneshot_session() -> AsyncGenerator[AsyncSession]:
    """Session on a throwaway engine, for sync code using asyncio.run.

    The cached get_engine() pool holds asyncpg connections bound to the
    event loop that created them. Sync callers that wrap each DB write in
    asyncio.run get a fresh loop every time, so a pooled connection from
    an earlier call raises "attached to a different loop" on reuse
    (pool_pre_ping does not catch loop affinity). NullPool plus dispose
    guarantees no connection outlives its loop.
    """
    url, connect_args = _translate_url(get_settings().database_url)
    engine = create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def session_dependency() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency that yields an AsyncSession for the request."""
    async with get_session() as session:
        yield session
