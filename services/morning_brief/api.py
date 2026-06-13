"""FastAPI surface for the morning-brief-api Modal app.

GET /health and /db-health stay open; POST /run-morning-brief (the manual
trigger, optional date override) requires bearer auth.
"""

from __future__ import annotations

import datetime  # noqa: TC003 (pydantic resolves RunSpec.date at runtime)
import traceback

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from services.morning_brief.orchestrator import run_with_metrics
from shared import __version__
from shared.auth import verify_bearer
from shared.health import build_health_router

api = FastAPI(title="morning-brief-api", version=__version__)
api.include_router(build_health_router())


class RunSpec(BaseModel):
    # pydantic parses the ISO string and turns a bad one into a proper 422.
    date: datetime.date | None = None


class RunResult(BaseModel):
    page_id: str
    tasks_today: int
    tasks_this_week: int
    tasks_overdue: int
    tasks_reschedule: int
    emails_count: int
    news_count: int
    duration_s: float


@api.post(
    "/run-morning-brief",
    response_model=RunResult,
    dependencies=[Depends(verify_bearer)],
)
def trigger_morning_brief(spec: RunSpec | None = None) -> RunResult:
    brief_date = spec.date if spec is not None else None
    try:
        result = run_with_metrics(today=brief_date)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=503, detail=f"morning brief failed: {type(exc).__name__}"
        ) from exc

    return RunResult(
        page_id=result.page_id,
        tasks_today=result.tasks_today,
        tasks_this_week=result.tasks_this_week,
        tasks_overdue=result.tasks_overdue,
        tasks_reschedule=result.tasks_reschedule,
        emails_count=result.emails_count,
        news_count=result.news_count,
        duration_s=result.duration_s,
    )
