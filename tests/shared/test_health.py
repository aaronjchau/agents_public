"""Behavior tests for the shared health router (shared.health)."""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import shared.health
from shared import __version__
from shared.health import build_health_router


def _client(*, include_db_health: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(build_health_router(include_db_health=include_db_health))
    return TestClient(app)


def _session_returning(scalar: Any) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = scalar
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


def _patch_get_session(session: MagicMock) -> Any:
    @asynccontextmanager
    async def fake_get_session() -> Any:
        yield session

    return patch.object(shared.health, "get_session", fake_get_session)


def test_health_returns_ok_and_version() -> None:
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_db_health_ok_when_select_1_round_trips() -> None:
    session = _session_returning(1)
    with _patch_get_session(session):
        response = _client().get("/db-health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_db_health_503_on_unexpected_scalar() -> None:
    session = _session_returning(0)
    with _patch_get_session(session):
        response = _client().get("/db-health")
    assert response.status_code == 503
    assert response.json()["detail"] == "unexpected db response"


def test_db_health_503_when_db_unreachable() -> None:
    session = MagicMock()
    session.execute = AsyncMock(side_effect=RuntimeError("boom"))
    with _patch_get_session(session):
        response = _client().get("/db-health")
    assert response.status_code == 503
    assert response.json()["detail"] == "database unreachable: RuntimeError"


def test_db_health_omitted_when_opted_out() -> None:
    client = _client(include_db_health=False)
    assert client.get("/health").status_code == 200
    assert client.get("/db-health").status_code == 404
