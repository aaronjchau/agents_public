"""Alembic environment for the agents service.

Targets the same Postgres as the running app: the database URL comes
from DATABASE_URL via shared.settings, not from alembic.ini.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from shared.db import Base, _translate_url
from shared.settings import get_settings

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Name new indexes ix_<table>_<column>; some earlier migrations predate this convention.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in offline mode, emitting SQL to stdout with no DB connection."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in online mode against the async engine.

    Uses _translate_url so Neon's sslmode=require query param becomes
    asyncpg connect_args (asyncpg rejects sslmode as a kwarg), the same
    translation shared.db.get_engine performs.
    """
    url, connect_args = _translate_url(get_settings().database_url)
    connectable = create_async_engine(
        url,
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Bridge from sync Alembic CLI to async engine via asyncio.run."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
