"""Tests for the Triager's Job Apps dispatch hook and failure audit.

End-to-end process_message coverage lives in test_classify_endpoint.py
and test_gmail_webhook.py.
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import Usage
from sqlalchemy.exc import IntegrityError

from services.triager.classifier import ClassificationResult
from services.triager.runner import process_message
from services.triager.types import Classification

if TYPE_CHECKING:
    from collections.abc import Iterator


# --------------------------------------------------------------- fixtures


def _gmail_service_with_message(
    *,
    message_id: str = "m-1",
    sender: str = "Recruiter <recruiter@bigco.com>",
    subject: str = "Phone screen",
    body_text: str = "Pick a time via Calendly.",
) -> MagicMock:
    """Build a Gmail service mock whose messages.get returns a parsed payload."""
    body_b64 = base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ascii")
    message = {
        "id": message_id,
        "threadId": "t-1",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Tue, 05 May 2026 12:00:00 +0000"},
            ],
            "body": {"data": body_b64},
        },
    }
    service = MagicMock()
    service.users.return_value.messages.return_value.get.return_value.execute.return_value = message
    return service


def _scalar_result(scalar_one_or_none: Any = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=scalar_one_or_none)
    return result


def _make_session(execute_returns: list[Any]) -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_returns)
    return session


def _patch_get_session(sessions: list[MagicMock]) -> Any:
    """Patch services.triager.runner.get_session to yield sessions in order."""
    sessions_iter: Iterator[MagicMock] = iter(sessions)

    @asynccontextmanager
    async def _get_session() -> Any:
        yield next(sessions_iter)

    return patch("services.triager.runner.get_session", new=_get_session)


def _classification(
    *,
    primary_label: str = "Job Apps",
    flagged: bool = True,
    reasoning: str = "Recruiter outreach.",
) -> Classification:
    return Classification(
        primary_label=primary_label,  # type: ignore[arg-type]
        flagged=flagged,
        reasoning=reasoning,
    )


def _classification_result(
    *,
    primary_label: str = "Job Apps",
    flagged: bool = True,
    reasoning: str = "Recruiter outreach.",
    input_tokens: int = 10,
    output_tokens: int = 10,
    cache_read_input_tokens: int = 0,
    model: str = "claude-sonnet-4-6",
) -> ClassificationResult:
    """Build a ClassificationResult shaped like what the classifier returns."""
    return ClassificationResult(
        classification=_classification(
            primary_label=primary_label, flagged=flagged, reasoning=reasoning
        ),
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        ),
        model=model,
    )


# ----------------------------------------------------------- dispatch tests


async def test_job_apps_label_dispatches_over_http() -> None:
    """A Job Apps label dispatches over HTTP with the message id only."""
    service = _gmail_service_with_message()
    result_obj = _classification_result(primary_label="Job Apps")

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    dispatch_client = AsyncMock()

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.triager.runner.classify", return_value=result_obj),
        patch("services.triager.runner.apply_classification") as label_apply,
        patch("shared.http_client.dispatch_job_apps", new=dispatch_client),
    ):
        result = await process_message("m-1", service=service)

    assert result.primary_label == "Job Apps"
    label_apply.assert_called_once()
    # No Gmail service crosses the HTTP boundary; Job Apps rebuilds its own.
    dispatch_client.assert_awaited_once_with("m-1")


async def test_non_job_apps_label_does_not_dispatch() -> None:
    """Other primary labels do not trigger the HTTP dispatch."""
    service = _gmail_service_with_message()
    result_obj = _classification_result(primary_label="Finance", flagged=False)

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    dispatch_client = AsyncMock()

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.triager.runner.classify", return_value=result_obj),
        patch("services.triager.runner.apply_classification"),
        patch("shared.http_client.dispatch_job_apps", new=dispatch_client),
    ):
        result = await process_message("m-fin", service=service)

    assert result.primary_label == "Finance"
    dispatch_client.assert_not_awaited()


async def test_job_apps_dispatch_failure_does_not_propagate() -> None:
    """A raise from the HTTP dispatch client does not fail the Triager response."""
    service = _gmail_service_with_message()
    result_obj = _classification_result(primary_label="Job Apps")

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    dispatch_client = AsyncMock(side_effect=RuntimeError("http blew up"))

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.triager.runner.classify", return_value=result_obj),
        patch("services.triager.runner.apply_classification"),
        patch("shared.http_client.dispatch_job_apps", new=dispatch_client),
    ):
        # Must not raise even though the HTTP client did.
        result = await process_message("m-err", service=service)

    assert result.primary_label == "Job Apps"
    dispatch_client.assert_awaited_once()


async def test_concurrent_duplicate_insert_does_not_raise_or_dispatch() -> None:
    """The duplicate-race loser returns cleanly without raising or dispatching.

    Rationale lives at the IntegrityError site in the runner.
    """
    service = _gmail_service_with_message()
    result_obj = _classification_result(primary_label="Job Apps")

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])
    audit_session.commit.side_effect = IntegrityError(
        "INSERT INTO triager_runs ...", None, Exception("duplicate key value")
    )

    dispatch_client = AsyncMock()

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.triager.runner.classify", return_value=result_obj),
        patch("services.triager.runner.apply_classification") as label_apply,
        patch("shared.http_client.dispatch_job_apps", new=dispatch_client),
    ):
        # Must not raise despite the duplicate-key IntegrityError.
        result = await process_message("m-dup", service=service)

    assert result.primary_label == "Job Apps"
    # The label was applied before the race (idempotent re-apply is fine);
    # the winner owns the dispatch, so the loser does not dispatch.
    label_apply.assert_called_once()
    dispatch_client.assert_not_awaited()


async def test_idempotent_replay_does_not_dispatch() -> None:
    """An existing triager_runs row skips both the classifier and the dispatch."""
    service = _gmail_service_with_message()

    existing = MagicMock()
    existing.message_id = "m-rerun"
    existing.primary_label = "Job Apps"
    existing.flagged = True
    existing.reasoning = "previously classified"
    existing.error = None

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=existing)])
    dispatch_client = AsyncMock()

    with (
        _patch_get_session([lookup_session]),
        patch("services.triager.runner.classify") as classifier,
        patch("services.triager.runner.apply_classification") as label_apply,
        patch("shared.http_client.dispatch_job_apps", new=dispatch_client),
    ):
        result = await process_message("m-rerun", service=service)

    classifier.assert_not_called()
    # The stored label is re-applied so Gmail stays consistent, but the
    # dispatch is not re-fired on idempotent runs.
    label_apply.assert_called_once()
    dispatch_client.assert_not_awaited()
    assert result.primary_label == "Job Apps"


# ------------------------------------------------------ failure audit tests


async def test_failure_writes_errored_audit_row() -> None:
    """A classify failure records an errored triager_runs row, then re-raises."""
    service = _gmail_service_with_message()

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    failure_session = _make_session([])
    failure_session.get = AsyncMock(return_value=None)

    with (
        _patch_get_session([lookup_session, failure_session]),
        patch(
            "services.triager.runner.classify",
            side_effect=RuntimeError("anthropic unavailable"),
        ),
        patch("services.triager.runner.apply_classification"),
        pytest.raises(RuntimeError, match="anthropic unavailable"),
    ):
        await process_message("m-fail", service=service)

    failure_session.add.assert_called_once()
    row = failure_session.add.call_args.args[0]
    assert row.message_id == "m-fail"
    assert row.primary_label is None
    assert row.error == "RuntimeError: anthropic unavailable"
    # Metadata parsed before the failure is preserved on the audit row.
    assert row.sender == "Recruiter <recruiter@bigco.com>"
    assert row.subject == "Phone screen"
    failure_session.commit.assert_awaited_once()


async def test_failure_audit_never_clobbers_existing_row() -> None:
    """If any row already exists for the message, the failure write is skipped."""
    service = _gmail_service_with_message()

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    failure_session = _make_session([])
    failure_session.get = AsyncMock(return_value=MagicMock())

    with (
        _patch_get_session([lookup_session, failure_session]),
        patch("services.triager.runner.classify", side_effect=RuntimeError("boom")),
        patch("services.triager.runner.apply_classification"),
        pytest.raises(RuntimeError),
    ):
        await process_message("m-fail", service=service)

    failure_session.add.assert_not_called()


async def test_errored_row_is_retried_and_replaced() -> None:
    """An errored row is deleted and the message reprocessed from scratch."""
    service = _gmail_service_with_message()
    result_obj = _classification_result(primary_label="News", flagged=False)

    errored = MagicMock()
    errored.message_id = "m-retry"
    errored.primary_label = None
    errored.error = "RuntimeError: anthropic unavailable"

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=errored)])
    delete_session = _make_session([MagicMock()])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, delete_session, audit_session]),
        patch("services.triager.runner.classify", return_value=result_obj) as classifier,
        patch("services.triager.runner.apply_classification") as label_apply,
    ):
        result = await process_message("m-retry", service=service)

    classifier.assert_called_once()
    label_apply.assert_called_once()
    delete_session.execute.assert_awaited_once()
    delete_session.commit.assert_awaited_once()
    audit_session.add.assert_called_once()
    assert result.primary_label == "News"
