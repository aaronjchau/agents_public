"""Modal entrypoint for the news-brief-api service.

Deploy: uv run modal deploy services/news_brief/modal_app.py
Dev: uv run modal serve services/news_brief/modal_app.py

Owns fastapi_asgi (the HTTP surface) and run_brief_cron (daily 9:15 AM ET).
Modal doesn't support uv workspaces, so the image runs uv_sync() against
the root pyproject.toml like every per-service app. News Brief keeps the
Node + ntn layer because the writer shells out to ntn for the Notion page;
triager and spend-sync skip it.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import modal

if TYPE_CHECKING:
    from fastapi import FastAPI

NTN_VERSION = "0.5.3"

app = modal.App("news-brief-api")

image = (
    modal.Image.debian_slim(python_version="3.12")
    # NodeSource setup script plus apt-installed nodejs gives a recent Node
    # and npm in one layer; Debian's stock nodejs package is too old for
    # ntn's dependency tree on slim images.
    .apt_install("curl", "ca-certificates", "gnupg")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
        # Pin nodejs for reproducible builds; bump when NodeSource advances 22.x.
        "apt-get install -y nodejs=22.22.3-1nodesource1",
        f"npm install -g ntn@{NTN_VERSION}",
    )
    .uv_sync()
    # Mount both the shared package tree and the services package so
    # "from services.news_brief.api import api" resolves on the worker.
    .add_local_python_source("shared", "services")
    # add_local_python_source copies .py files only, so the curator's .md
    # system prompt (kept editable without a rebuild) must be mounted
    # explicitly to the on-container path the curator resolves at runtime.
    .add_local_dir(
        "services/news_brief/prompts",
        remote_path="/root/services/news_brief/prompts",
    )
    # Alembic migrations and config. The spend-sync service owns the
    # run_migrations one-shot, but having the files available everywhere
    # keeps the dev experience consistent.
    .add_local_dir("migrations", remote_path="/root/migrations")
    .add_local_file("alembic.ini", remote_path="/root/alembic.ini")
)

secrets = [modal.Secret.from_name("agents-secrets")]


@app.function(image=image, secrets=secrets)
@modal.asgi_app()
def fastapi_asgi() -> "FastAPI":
    from services.news_brief.api import api

    return api


@app.function(
    image=image,
    secrets=secrets,
    schedule=modal.Cron("15 9 * * *", timezone="America/New_York"),
    timeout=600,
)
def run_brief_cron() -> str:
    """Run the daily news brief; return the new Notion page ID.

    The window is 24 hours back from invocation. The manual endpoint's
    default (10 AM ET yesterday) differs by ~45 minutes; harmless, since
    newsletters publish on daily cadences and both windows catch the same
    editions.
    """
    from services.news_brief.orchestrator import run_news_brief

    until = datetime.now(tz=UTC)
    since = until - timedelta(hours=24)
    return run_news_brief(since=since, until=until)
