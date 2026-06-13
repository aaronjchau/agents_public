"""Modal entrypoint for the job-apps-api service.

Deploy with uv run modal deploy services/job_apps/modal_app.py (serve
for dev). One function, fastapi_asgi, hosts the HTTP surface; no crons.
Modal lacks uv workspace support, so the image runs uv_sync against the
root pyproject.toml.
"""

from typing import TYPE_CHECKING

import modal

if TYPE_CHECKING:
    from fastapi import FastAPI

NTN_VERSION = "0.5.3"

app = modal.App("job-apps-api")

image = (
    modal.Image.debian_slim(python_version="3.12")
    # NodeSource setup plus apt nodejs gives a recent Node and npm in
    # one layer; Debian's stock nodejs is too old for ntn's dependency
    # tree. Required because notion_match.py and notion_writer.py shell
    # out to ntn.
    .apt_install("curl", "ca-certificates", "gnupg")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
        # Pin nodejs for reproducible builds; bump when NodeSource advances 22.x.
        "apt-get install -y nodejs=22.22.3-1nodesource1",
        f"npm install -g ntn@{NTN_VERSION}",
    )
    .uv_sync()
    # Mount both package trees so the service imports resolve on the
    # worker.
    .add_local_python_source("shared", "services")
    # add_local_python_source copies .py files only; the classifier
    # loads its .md prompts at runtime, so mount them at the path the
    # loader expects.
    .add_local_dir(
        "services/job_apps/prompts",
        remote_path="/root/services/job_apps/prompts",
    )
    # Alembic files are mounted in every service image for a consistent
    # dev experience; this service does not run migrations.
    .add_local_dir("migrations", remote_path="/root/migrations")
    .add_local_file("alembic.ini", remote_path="/root/alembic.ini")
)

secrets = [modal.Secret.from_name("agents-secrets")]


@app.function(image=image, secrets=secrets)
@modal.asgi_app()
def fastapi_asgi() -> "FastAPI":
    from services.job_apps.api import api

    return api
