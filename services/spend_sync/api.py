"""FastAPI surface for the spend-sync-api Modal app.

Exists so Modal serves /health for the smoke checks; the real work lives
in the crons and one-shots on modal_app.py. /db-health is intentionally
omitted: the crons already log failures in Modal and the dashboard reads
Neon directly, so no consumer needs a DB probe scoped to this service.
"""

from __future__ import annotations

from fastapi import FastAPI

from shared import __version__
from shared.health import build_health_router

api = FastAPI(title="spend-sync-api", version=__version__)
# Deliberately no /db-health here; see the module docstring.
api.include_router(build_health_router(include_db_health=False))
