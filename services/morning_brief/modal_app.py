"""Modal entrypoint for the morning-brief-api service.

Hosts the HTTP surface plus the daily run_brief_cron. The image carries
the Node + ntn layer for Notion writes; ntn is pinned to the version
verified to support the page-markdown GET and PATCH replace_content
endpoints the brief relies on (news-brief's older pin only needs create).
"""

from typing import TYPE_CHECKING

import modal

if TYPE_CHECKING:
    from fastapi import FastAPI

NTN_VERSION = "0.15.1"

app = modal.App("morning-brief-api")

image = (
    modal.Image.debian_slim(python_version="3.12")
    # NodeSource gives a recent Node + npm in one layer; Debian's stock
    # nodejs is too old for ntn's dependency tree on slim images.
    .apt_install("curl", "ca-certificates", "gnupg")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
        # Pin nodejs for reproducible builds; bump when NodeSource advances 22.x.
        "apt-get install -y nodejs=22.22.3-1nodesource1",
        f"npm install -g ntn@{NTN_VERSION}",
    )
    .uv_sync()
    # add_local_python_source copies only .py files; mount both the shared
    # and services packages so imports resolve on the worker.
    .add_local_python_source("shared", "services")
    # .md prompts aren't picked up by the python-source path; mount them to
    # the on-container path the LLM stage resolves at runtime.
    .add_local_dir(
        "services/morning_brief/prompts",
        remote_path="/root/services/morning_brief/prompts",
    )
    .add_local_dir("migrations", remote_path="/root/migrations")
    .add_local_file("alembic.ini", remote_path="/root/alembic.ini")
)

secrets = [modal.Secret.from_name("agents-secrets")]


@app.function(image=image, secrets=secrets)
@modal.asgi_app()
def fastapi_asgi() -> "FastAPI":
    from services.morning_brief.api import api

    return api


@app.function(
    image=image,
    secrets=secrets,
    schedule=modal.Cron("30 10 * * *", timezone="America/New_York"),
    timeout=600,
)
def run_brief_cron() -> str:
    """Run the daily brief and return the Notion page id.

    The 10:30 AM ET slot sits after the 9:15 News Brief so today's News
    page exists for the Major News section.
    """
    from services.morning_brief.orchestrator import run_morning_brief

    return run_morning_brief()
