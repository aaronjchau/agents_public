"""FastAPI surface for the news-brief-api Modal app.

Owns /health and /db-health (the per-service liveness and DB probes) plus
/run-news-brief, the manual trigger used by the dashboard force re-run
button and when the daily cron misses a window.
"""

from __future__ import annotations

import traceback
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import AwareDatetime, BaseModel

from services.news_brief.orchestrator import run_with_metrics
from shared import __version__
from shared.auth import verify_bearer
from shared.health import build_health_router

api = FastAPI(title="news-brief-api", version=__version__)
api.include_router(build_health_router())

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class WindowSpec(BaseModel):
    """Optional manual window.

    AwareDatetime rejects naive datetimes with a structured 422; the
    fetcher's window filter compares tz-aware datetimes, so a naive value
    would otherwise surface as an opaque 503.
    """

    since_iso: AwareDatetime | None = None
    until_iso: AwareDatetime | None = None


class RunResult(BaseModel):
    page_id: str
    story_count: int
    email_count: int
    duration_s: float


def _default_since() -> datetime:
    """Return 10 AM ET yesterday in UTC, the manual trigger's default lower bound.

    The production cron uses a plain 24h-back window instead, which starts
    ~45 minutes earlier when it fires at 9:15 AM ET; newsletters publish on
    daily cadences, so both windows catch the same editions. zoneinfo keeps
    DST transitions on America/New_York rather than a fixed offset.
    """
    now_et = datetime.now(tz=ET)
    yesterday_et = (now_et - timedelta(days=1)).date()
    since_et = datetime.combine(yesterday_et, dtime(10, 0), tzinfo=ET)
    return since_et.astimezone(UTC)


# ---------------------------------------------------------------------------
# Manual brief replay
# ---------------------------------------------------------------------------


@api.post(
    "/run-news-brief",
    response_model=RunResult,
    dependencies=[Depends(verify_bearer)],
)
def trigger_news_brief(window: WindowSpec | None = None) -> RunResult:
    """Manually trigger the news-brief flow.

    since_iso defaults to 10 AM ET yesterday and until_iso to now (UTC).
    An orchestrator failure maps to a 503 carrying the exception class
    name, same as /db-health, so operational failures are never reported
    as 500.
    """
    spec = window or WindowSpec()
    since = spec.since_iso if spec.since_iso is not None else _default_since()
    until = spec.until_iso

    try:
        result = run_with_metrics(since=since, until=until)
    except Exception as exc:  # Mirror /db-health: any orchestrator failure becomes 503.
        # Print the full traceback so Modal logs keep the diagnostic this
        # broad except would otherwise swallow.
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"news brief failed: {type(exc).__name__}",
        ) from exc

    return RunResult(
        page_id=result.page_id,
        story_count=result.story_count,
        email_count=result.email_count,
        duration_s=result.duration_s,
    )
