"""Tests for the POST /classify-email shim: exception-to-status mapping only.

The fetch / classify / apply / audit logic is covered in test_runner.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from services.triager.api import api
from services.triager.types import Classification

client = TestClient(api)


def _classification(
    *,
    primary_label: str = "Finance",
    flagged: bool = True,
    reasoning: str = "Statement requires no action but is finance-relevant.",
) -> Classification:
    return Classification(
        primary_label=primary_label,  # type: ignore[arg-type]
        flagged=flagged,
        reasoning=reasoning,
    )


def test_classify_email_happy_path_returns_classification_fields() -> None:
    with patch(
        "services.triager.api.process_message",
        new=AsyncMock(return_value=_classification()),
    ) as mock_process:
        response = client.post("/classify-email", json={"message_id": "abc"})

    assert response.status_code == 200
    assert response.json() == {
        "primary_label": "Finance",
        "flagged": True,
        "reasoning": "Statement requires no action but is finance-relevant.",
    }
    mock_process.assert_awaited_once_with("abc")


def test_classify_email_returns_404_when_gmail_says_not_found() -> None:
    resp = MagicMock()
    resp.status = 404
    resp.reason = "Not Found"
    err = HttpError(resp, b'{"error": "not found"}')
    with patch(
        "services.triager.api.process_message",
        new=AsyncMock(side_effect=err),
    ):
        response = client.post("/classify-email", json={"message_id": "missing"})

    assert response.status_code == 404
    assert "missing" in response.json()["detail"]


def test_classify_email_non_404_http_error_returns_503() -> None:
    resp = MagicMock()
    resp.status = 500
    resp.reason = "Internal Server Error"
    err = HttpError(resp, b'{"error": "boom"}')
    with patch(
        "services.triager.api.process_message",
        new=AsyncMock(side_effect=err),
    ):
        response = client.post("/classify-email", json={"message_id": "id"})

    assert response.status_code == 503
    assert "HttpError" in response.json()["detail"]


def test_classify_email_generic_exception_returns_503() -> None:
    with patch(
        "services.triager.api.process_message",
        new=AsyncMock(side_effect=RuntimeError("anthropic blew up")),
    ):
        response = client.post("/classify-email", json={"message_id": "m"})

    assert response.status_code == 503
    assert "RuntimeError" in response.json()["detail"]


def test_classify_email_missing_message_id_returns_422() -> None:
    response = client.post("/classify-email", json={})
    assert response.status_code == 422
