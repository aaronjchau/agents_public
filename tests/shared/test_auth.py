"""Tests for the dashboard bearer-token dependency."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from shared.auth import verify_bearer
from shared.settings import get_settings


def _make_app() -> FastAPI:
    """Build a tiny FastAPI app exposing one bearer-guarded route."""
    app = FastAPI()

    @app.get("/guarded", dependencies=[Depends(verify_bearer)])
    def _guarded() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Any:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_passthrough_when_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No AGENTS_API_TOKEN configured: local dev convenience."""
    monkeypatch.delenv("AGENTS_API_TOKEN", raising=False)
    client = TestClient(_make_app())

    response = client.get("/guarded")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_passthrough_with_token_unset_ignores_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Header presence is irrelevant when the token isn't configured."""
    monkeypatch.delenv("AGENTS_API_TOKEN", raising=False)
    client = TestClient(_make_app())

    response = client.get("/guarded", headers={"Authorization": "Bearer anything"})
    assert response.status_code == 200


def test_fails_closed_when_token_unset_and_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset token in a remote Modal container is a misconfiguration: fail closed (500)."""
    monkeypatch.delenv("AGENTS_API_TOKEN", raising=False)
    monkeypatch.setenv("MODAL_IS_REMOTE", "1")
    get_settings.cache_clear()
    client = TestClient(_make_app(), raise_server_exceptions=False)

    response = client.get("/guarded")
    assert response.status_code == 500
    assert "bearer secret unset" in response.json()["detail"]


def test_rejects_missing_header_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTS_API_TOKEN", "sk-dashboard-secret")
    client = TestClient(_make_app())

    response = client.get("/guarded")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_rejects_malformed_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTS_API_TOKEN", "sk-dashboard-secret")
    client = TestClient(_make_app())

    response = client.get("/guarded", headers={"Authorization": "sk-dashboard-secret"})
    assert response.status_code == 401


def test_rejects_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTS_API_TOKEN", "sk-dashboard-secret")
    client = TestClient(_make_app())

    response = client.get("/guarded", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_accepts_matching_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTS_API_TOKEN", "sk-dashboard-secret")
    client = TestClient(_make_app())

    response = client.get("/guarded", headers={"Authorization": "Bearer sk-dashboard-secret"})
    assert response.status_code == 200


def test_accepts_token_with_surrounding_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bearer scheme keeps the token portion stripped, so trailing whitespace
    in the header doesn't reject a valid token."""
    monkeypatch.setenv("AGENTS_API_TOKEN", "sk-dashboard-secret")
    client = TestClient(_make_app())

    response = client.get("/guarded", headers={"Authorization": "Bearer sk-dashboard-secret  "})
    assert response.status_code == 200


def test_rejects_non_ascii_token_without_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-ASCII bearer token yields a clean 401 instead of a TypeError 500.

    Starlette latin-1-decodes raw header bytes, so the token is attacker-
    controllable down to bytes 0x80-0xFF; secrets.compare_digest on str
    raises TypeError there. The dependency encodes to bytes to stay a 401.
    TestClient ascii-encodes header values, so call the dependency directly.
    """
    monkeypatch.setenv("AGENTS_API_TOKEN", "sk-dashboard-secret")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc_info:
        verify_bearer(authorization="Bearer \xe9")
    assert exc_info.value.status_code == 401
