"""Smoke test that every per-service FastAPI surface serves the health routes.

Pins the GET /health contract per service, plus which surfaces expose
/db-health (spend-sync deliberately omits it).
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.job_apps.api import api as job_apps_api
from services.morning_brief.api import api as morning_brief_api
from services.news_brief.api import api as news_brief_api
from services.spend_sync.api import api as spend_sync_api
from services.triager.api import api as triager_api
from shared import __version__


def _route_paths(api: FastAPI) -> set[str]:
    return {getattr(route, "path", "") for route in api.routes}


def _assert_health(api: FastAPI, *, expect_db_health: bool) -> None:
    response = TestClient(api).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
    assert ("/db-health" in _route_paths(api)) is expect_db_health


def test_health_routes_on_triager_api() -> None:
    _assert_health(triager_api, expect_db_health=True)


def test_health_routes_on_job_apps_api() -> None:
    _assert_health(job_apps_api, expect_db_health=True)


def test_health_routes_on_news_brief_api() -> None:
    _assert_health(news_brief_api, expect_db_health=True)


def test_health_routes_on_morning_brief_api() -> None:
    _assert_health(morning_brief_api, expect_db_health=True)


def test_health_routes_on_spend_sync_api() -> None:
    _assert_health(spend_sync_api, expect_db_health=False)
