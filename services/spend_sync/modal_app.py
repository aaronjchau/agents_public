"""Modal entrypoint for the spend-sync-api service.

Owns the daily spend cron, a manual backfill one-shot, and the Alembic
run_migrations one-shot (this image is the lightest, so cold starts for
ad-hoc ops are cheapest here). Skips the Node + ntn layer: nothing here
writes to Notion. Design notes: docs/design.md.
"""

from typing import TYPE_CHECKING

import modal

if TYPE_CHECKING:
    from fastapi import FastAPI

app = modal.App("spend-sync-api")

image = (
    modal.Image.debian_slim(python_version="3.12")
    # Modal doesn't support uv workspaces, so uv_sync() installs the root
    # pyproject's full dep set; narrower per-service lists are deferred.
    .uv_sync()
    # add_local_python_source copies only .py files; mount both the shared
    # and services packages so imports resolve on the worker.
    .add_local_python_source("shared", "services")
    # Alembic config and migrations, mounted so run_migrations can apply
    # pending schema changes against Neon from inside the deployed image.
    .add_local_dir("migrations", remote_path="/root/migrations")
    .add_local_file("alembic.ini", remote_path="/root/alembic.ini")
)

secrets = [modal.Secret.from_name("agents-secrets")]


@app.function(image=image, secrets=secrets)
@modal.asgi_app()
def fastapi_asgi() -> "FastAPI":
    from services.spend_sync.api import api

    return api


@app.function(
    image=image,
    secrets=secrets,
    schedule=modal.Cron("0 4 * * *", timezone="America/New_York"),
    timeout=300,
)
async def sync_anthropic_spend_cron() -> str:
    """Pull yesterday's Anthropic spend into anthropic_spend, daily 4 AM ET.

    A yesterday-only window keeps the row final (Anthropic settles billing
    within ~24h). No-ops when ANTHROPIC_ADMIN_API_KEY is unset, so the
    schedule stays green until the admin key is provisioned.
    """
    from datetime import UTC, datetime, timedelta

    from shared.anthropic_admin import sync_anthropic_spend

    # Explicit UTC so the window doesn't depend on the cron firing at 4 AM ET.
    yesterday = datetime.now(tz=UTC).date() - timedelta(days=1)
    n = await sync_anthropic_spend(since=yesterday, until=yesterday)
    return f"upserted {n} rows for {yesterday}"


@app.function(image=image, secrets=secrets, timeout=600)
async def sync_anthropic_spend_oneshot(since_iso: str, until_iso: str) -> str:
    """Manually backfill anthropic_spend for an arbitrary window.

    Trigger whenever a re-pull is needed:

        uv run modal run services/spend_sync/modal_app.py::sync_anthropic_spend_oneshot \\
            --since-iso 2026-05-04 --until-iso 2026-05-16

    Idempotent: the underlying UPSERT replaces same-day rows. Same no-op
    behavior as the cron when the admin key is unset.
    """
    from datetime import date

    from shared.anthropic_admin import sync_anthropic_spend

    n = await sync_anthropic_spend(
        since=date.fromisoformat(since_iso),
        until=date.fromisoformat(until_iso),
    )
    return f"upserted {n} rows for {since_iso}..{until_iso}"


@app.function(image=image, secrets=secrets, timeout=300)
def run_migrations() -> str:
    """Apply pending Alembic migrations against the live Postgres.

    Trigger after each schema-changing PR ships:

        uv run modal run services/spend_sync/modal_app.py::run_migrations

    Idempotent: running with no pending migrations is a no-op. Stdout
    from Alembic surfaces in Modal logs; failures raise.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config("/root/alembic.ini")
    command.upgrade(cfg, "head")
    return "alembic upgrade head: complete"
