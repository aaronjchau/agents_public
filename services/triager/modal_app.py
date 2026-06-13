"""Modal entrypoint for the triager-api service.

Deploy: uv run modal deploy services/triager/modal_app.py
Dev: uv run modal serve services/triager/modal_app.py

Hosts the FastAPI surface, the watch-renewal cron, and one-shot helpers.
Modal does not support uv workspaces, so the image runs uv_sync() against
the root pyproject.toml; the triager skips the Node + ntn layer because
it never writes to Notion.
"""

from typing import TYPE_CHECKING, Any

import modal

if TYPE_CHECKING:
    from fastapi import FastAPI

app = modal.App("triager-api")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_sync()
    # Mount both the shared package tree and the services package so
    # from services.triager.api import api resolves on the worker.
    .add_local_python_source("shared", "services")
    # add_local_python_source copies .py files only. The classifier loads
    # its .md prompts from the package directory at runtime, so mount
    # them to the on-container path the loader expects.
    .add_local_dir(
        "services/triager/prompts",
        remote_path="/root/services/triager/prompts",
    )
    # Alembic migrations + config. The spend-sync service owns the
    # run_migrations one-shot, but having the files available everywhere
    # keeps the dev experience consistent.
    .add_local_dir("migrations", remote_path="/root/migrations")
    .add_local_file("alembic.ini", remote_path="/root/alembic.ini")
)

secrets = [modal.Secret.from_name("agents-secrets")]


@app.function(image=image, secrets=secrets)
@modal.asgi_app()
def fastapi_asgi() -> "FastAPI":
    from services.triager.api import api

    return api


@app.function(image=image, secrets=secrets, timeout=120)
async def start_watch_oneshot() -> dict[str, Any]:
    """One-shot bootstrap of the Gmail Pub/Sub watch.

    Run after deploying the Pub/Sub topic + service-account permissions:

        uv run modal run services/triager/modal_app.py::start_watch_oneshot

    Idempotent: re-running just resets the watch with a fresh expiration.
    """
    from services.triager.watch import start_watch

    state = await start_watch()
    return state.model_dump(mode="json")


@app.function(
    image=image,
    secrets=secrets,
    schedule=modal.Cron("0 9 * * 1,4", timezone="America/New_York"),
    timeout=120,
)
async def renew_watch_cron() -> dict[str, Any]:
    """Renew the Gmail Pub/Sub watch on a Mon+Thu cadence.

    Gmail's users.watch() expires after seven days, and a missed renewal
    silently drops events (mail keeps arriving but no webhook fires).
    Firing Mondays and Thursdays at 9 AM ET caps the gap between calls at
    four days, a comfortable buffer under the 7-day TTL even if one run
    fails. Each call idempotently extends the existing watch.
    """
    from services.triager.watch import renew_watch

    state = await renew_watch()
    return state.model_dump(mode="json")


@app.function(image=image, secrets=secrets, timeout=900)
async def backfill_email_received_at() -> str:
    """One-shot backfill of email_received_at on existing audit rows.

    Run after the email_received_at migration:

        uv run modal run services/triager/modal_app.py::backfill_email_received_at

    Idempotent: only touches rows where the column is NULL. Uses
    messages.get with format='metadata' (5 quota units each) so Gmail
    quota usage stays trivial.
    """
    from shared.backfill_email_received_at import run

    return await run()
