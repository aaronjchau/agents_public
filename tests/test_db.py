import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from shared.db import (
    AnthropicSpend,
    Base,
    GmailWatchState,
    MorningBriefRun,
    TriagerRun,
    _translate_url,
    get_engine,
    get_oneshot_session,
    get_session_factory,
)


def test_base_is_declarative() -> None:
    assert issubclass(Base, DeclarativeBase)


def test_engine_is_async_and_cached() -> None:
    engine = get_engine()
    assert isinstance(engine, AsyncEngine)
    assert engine is get_engine()


def test_session_factory_is_cached() -> None:
    assert get_session_factory() is get_session_factory()


def test_oneshot_session_builds_and_disposes_fresh_engine_per_loop() -> None:
    """Each oneshot session builds and disposes its own NullPool engine,
    so no connection outlives the asyncio.run loop that created it."""
    engines: list[MagicMock] = []

    def fake_create_engine(url: object, **kwargs: object) -> MagicMock:
        assert kwargs.get("poolclass") is NullPool
        engine = MagicMock()
        engine.dispose = AsyncMock()
        engines.append(engine)
        return engine

    session = MagicMock()
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    fake_factory = MagicMock(return_value=session_cm)

    async def use_once() -> None:
        async with get_oneshot_session() as s:
            assert s is session

    with (
        patch("shared.db.create_async_engine", side_effect=fake_create_engine),
        patch("shared.db.async_sessionmaker", return_value=fake_factory),
    ):
        # Two sequential asyncio.run calls = two distinct event loops,
        # exactly the warm-container double audit-write shape.
        asyncio.run(use_once())
        asyncio.run(use_once())

    assert len(engines) == 2
    for engine in engines:
        engine.dispose.assert_awaited_once()


def test_translate_url_strips_sslmode_require() -> None:
    """Neon's psycopg2-style ?sslmode=require must become ssl=True for asyncpg."""
    url, args = _translate_url("postgresql+asyncpg://u:p@h.neon.tech/db?sslmode=require")
    assert "sslmode" not in url.query
    assert args == {"ssl": True}


def test_translate_url_preserves_other_query_params() -> None:
    url, _ = _translate_url(
        "postgresql+asyncpg://u:p@h.neon.tech/db?sslmode=require&application_name=agents"
    )
    assert url.query == {"application_name": "agents"}


def test_translate_url_no_sslmode_passes_through() -> None:
    url, args = _translate_url("postgresql+asyncpg://u:p@localhost:5432/db")
    assert args == {}
    assert "sslmode" not in url.query


def test_translate_url_sslmode_disable_omits_ssl_arg() -> None:
    url, args = _translate_url("postgresql+asyncpg://u:p@localhost:5432/db?sslmode=disable")
    assert "sslmode" not in url.query
    assert args == {}


def test_translate_url_upgrades_plain_postgresql_to_asyncpg() -> None:
    """Plain postgresql:// upgrades to +asyncpg so one URL serves every runtime."""
    url, args = _translate_url("postgresql://u:p@h.neon.tech/db?sslmode=require")
    assert url.drivername == "postgresql+asyncpg"
    assert "sslmode" not in url.query
    assert args == {"ssl": True}


def test_translate_url_leaves_explicit_driver_alone() -> None:
    """If the caller wrote +asyncpg already, don't touch the drivername."""
    url, _ = _translate_url("postgresql+asyncpg://u:p@h.neon.tech/db")
    assert url.drivername == "postgresql+asyncpg"


def test_gmail_watch_state_registered() -> None:
    assert "gmail_watch_state" in Base.metadata.tables
    table = Base.metadata.tables["gmail_watch_state"]
    assert {col.name for col in table.primary_key.columns} == {"email"}
    assert {col.name for col in table.columns} == {
        "email",
        "current_history_id",
        "expires_at",
        "last_renewed_at",
    }


def test_triager_runs_registered() -> None:
    assert "triager_runs" in Base.metadata.tables
    table = Base.metadata.tables["triager_runs"]
    assert {col.name for col in table.primary_key.columns} == {"message_id"}
    expected = {
        "message_id",
        "classified_at",
        "email_received_at",
        "primary_label",
        "flagged",
        "subject",
        "sender",
        "reasoning",
        "latency_ms",
        "error",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_5m",
        "cache_creation_1h",
        "cost_usd",
        "stage_timings_ms",
    }
    assert {col.name for col in table.columns} == expected


def test_triager_runs_classified_at_indexed_via_migration() -> None:
    """A migration creates the classified_at index the dashboard queries rely on.

    The model doesn't declare the index, so the migration source is
    checked via AST for the op.create_index call.
    """
    versions_dir = Path(__file__).parent.parent / "migrations" / "versions"
    for migration in versions_dir.glob("*.py"):
        for node in ast.walk(ast.parse(migration.read_text())):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_index"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "op"
            ):
                continue
            name_arg = node.args[0]
            if isinstance(name_arg, ast.Constant) and name_arg.value == (
                "ix_triager_runs_classified_at"
            ):
                assert ast.literal_eval(node.args[1]) == "triager_runs"
                assert ast.literal_eval(node.args[2]) == ["classified_at"]
                return
    pytest.fail("no migration creates ix_triager_runs_classified_at")


def test_anthropic_spend_registered() -> None:
    assert "anthropic_spend" in Base.metadata.tables
    table = Base.metadata.tables["anthropic_spend"]
    assert {col.name for col in table.primary_key.columns} == {"spend_date", "model"}
    expected = {
        "spend_date",
        "model",
        "cost_usd",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_5m",
        "cache_write_1h",
        "pulled_at",
    }
    assert {col.name for col in table.columns} == expected


def test_morning_brief_runs_registered() -> None:
    assert "morning_brief_runs" in Base.metadata.tables
    table = Base.metadata.tables["morning_brief_runs"]
    assert {col.name for col in table.primary_key.columns} == {"brief_date"}
    expected = {
        "brief_date",
        "ran_at",
        "tasks_today",
        "tasks_this_week",
        "tasks_overdue",
        "tasks_reschedule",
        "emails_count",
        "news_stories",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cost_usd",
        "duration_s",
        "notion_page_id",
        "errored",
        "error_msg",
    }
    assert {col.name for col in table.columns} == expected


def test_models_use_shared_base() -> None:
    assert issubclass(GmailWatchState, Base)
    assert issubclass(TriagerRun, Base)
    assert issubclass(AnthropicSpend, Base)
    assert issubclass(MorningBriefRun, Base)
