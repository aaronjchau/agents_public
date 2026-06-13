"""Shared /health and /db-health probes for the per-service apps.

Every service mounts the same router via build_health_router so probe
semantics can't drift between copies.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from shared import __version__
from shared.db import get_session


class Health(BaseModel):
    status: str
    version: str


def build_health_router(*, include_db_health: bool = True) -> APIRouter:
    """Build a router serving /health and, unless opted out, /db-health.

    include_db_health=False is for services where no consumer needs a DB
    probe; spend_sync documents its omission in services/spend_sync/api.py.
    """
    router = APIRouter()

    @router.get("/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok", version=__version__)

    if include_db_health:

        @router.get("/db-health", response_model=Health)
        async def db_health() -> Health:
            """Round-trip a SELECT 1 against Postgres; 503 if unreachable.

            Catches Exception broadly because asyncpg raises driver-level
            errors that aren't SQLAlchemyError subclasses, and a health
            probe must never bubble a 500: failure is the answer it
            exists to give.
            """
            try:
                async with get_session() as session:
                    result = await session.execute(text("SELECT 1"))
                    if result.scalar() != 1:
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="unexpected db response",
                        )
            except HTTPException:
                raise
            except Exception as exc:  # Health probes catch broadly; any failure is a 503.
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"database unreachable: {type(exc).__name__}",
                ) from exc
            return Health(status="ok", version=__version__)

    return router
