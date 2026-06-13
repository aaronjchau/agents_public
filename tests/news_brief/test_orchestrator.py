"""Integration tests for run_news_brief() with all stages mocked.

Covers the call sequence, the empty-day short-circuit, stage-attributed
failure propagation, and the news_brief_runs audit UPSERT.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from anthropic.types import Usage

from services.news_brief.fetcher import FetchedEmail
from services.news_brief.orchestrator import run_news_brief, run_with_metrics
from services.news_brief.types import CurateResult, Link, ParsedEmail, StoryCandidate

SINCE = datetime(2026, 5, 2, 14, 0, tzinfo=UTC)
UNTIL = datetime(2026, 5, 3, 13, 0, tzinfo=UTC)
EXPECTED_BRIEF_DATE = date(2026, 5, 3)


def _fetched(message_id: str = "<m1@x>") -> FetchedEmail:
    return FetchedEmail(
        message_id=message_id,
        sender_slug="bloomberg",
        subject="Evening Briefing",
        received_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
        body_html="<html><body><a href='https://x.test/a'>headline</a></body></html>",
    )


def _parsed(message_id: str = "<m1@x>") -> ParsedEmail:
    return ParsedEmail(
        message_id=message_id,
        sender="bloomberg",
        subject="Evening Briefing",
        received_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
        plain_text="Stuff happened [1] today.",
        links=[Link(id=1, url="https://x.test/a", anchor_text="headline")],
    )


def _story(source_email_id: str = "<m1@x>") -> StoryCandidate:
    return StoryCandidate(
        category="Markets",
        label="Fed",
        summary="Cut rates by 25bps.",
        source_email_id=source_email_id,
        link_id=1,
    )


def _curate_result(stories: list[StoryCandidate] | None = None) -> CurateResult:
    """Build a curator result with deterministic token counts for assertions."""
    return CurateResult(
        stories=stories if stories is not None else [_story()],
        usage=Usage(
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=200,
        ),
        model="claude-sonnet-4-6",
    )


@patch("services.news_brief.orchestrator._upsert_audit_row")
@patch("services.news_brief.orchestrator.write_brief")
@patch("services.news_brief.orchestrator.compose")
@patch("services.news_brief.orchestrator.curate")
@patch("services.news_brief.orchestrator.parse_email_html")
@patch("services.news_brief.orchestrator.fetch_news_emails")
def test_run_news_brief_happy_path(
    fetch_mock: MagicMock,
    parse_mock: MagicMock,
    curate_mock: MagicMock,
    compose_mock: MagicMock,
    write_mock: MagicMock,
    audit_mock: MagicMock,
) -> None:
    fetch_mock.return_value = [_fetched("<m1@x>"), _fetched("<m2@x>")]
    parse_mock.side_effect = [_parsed("<m1@x>"), _parsed("<m2@x>")]
    curate_mock.return_value = _curate_result([_story()])
    compose_mock.return_value = '## Markets {color="gray_bg"}\n> **Fed:** ...'
    write_mock.return_value = "page-abc"

    page_id = run_news_brief(since=SINCE, until=UNTIL)

    assert page_id == "page-abc"
    fetch_mock.assert_called_once_with(since=SINCE, until=UNTIL)
    assert parse_mock.call_count == 2
    curate_mock.assert_called_once()
    compose_mock.assert_called_once()
    write_mock.assert_called_once()
    write_kwargs = write_mock.call_args.kwargs
    assert write_kwargs["markdown_body"].startswith("## Markets")
    assert write_kwargs["brief_date"] == EXPECTED_BRIEF_DATE

    # Audit row written with token + cost data from the CurateResult.
    audit_mock.assert_called_once()
    audit_values = audit_mock.call_args.args[0]
    assert audit_values["brief_date"] == EXPECTED_BRIEF_DATE
    assert audit_values["emails_fetched"] == 2
    assert audit_values["stories_considered"] == 2
    assert audit_values["stories_included"] == 1
    assert audit_values["model"] == "claude-sonnet-4-6"
    assert audit_values["input_tokens"] == 1000
    assert audit_values["output_tokens"] == 500
    assert audit_values["cache_read_tokens"] == 200
    assert audit_values["cost_usd"] is not None
    assert audit_values["cost_usd"] > Decimal("0")
    assert audit_values["notion_page_id"] == "page-abc"
    assert audit_values["errored"] is False
    assert audit_values["error_msg"] is None


@patch("services.news_brief.orchestrator._upsert_audit_row")
@patch("services.news_brief.orchestrator.write_brief")
@patch("services.news_brief.orchestrator.compose")
@patch("services.news_brief.orchestrator.curate")
@patch("services.news_brief.orchestrator.parse_email_html")
@patch("services.news_brief.orchestrator.fetch_news_emails")
def test_run_news_brief_no_emails_short_circuits(
    fetch_mock: MagicMock,
    parse_mock: MagicMock,
    curate_mock: MagicMock,
    compose_mock: MagicMock,
    write_mock: MagicMock,
    audit_mock: MagicMock,
) -> None:
    fetch_mock.return_value = []
    compose_mock.return_value = "No newsworthy stories today."
    write_mock.return_value = "page-empty"

    page_id = run_news_brief(since=SINCE, until=UNTIL)

    assert page_id == "page-empty"
    parse_mock.assert_not_called()
    curate_mock.assert_not_called()
    compose_mock.assert_called_once_with([], [])
    write_mock.assert_called_once()

    # Audit row written with null LLM columns (no curator call was made).
    audit_mock.assert_called_once()
    audit_values = audit_mock.call_args.args[0]
    assert audit_values["emails_fetched"] == 0
    assert audit_values["stories_considered"] == 0
    assert audit_values["stories_included"] == 0
    assert audit_values["model"] is None
    assert audit_values["input_tokens"] is None
    assert audit_values["cost_usd"] is None
    assert audit_values["notion_page_id"] == "page-empty"
    assert audit_values["errored"] is False


@patch("services.news_brief.orchestrator._upsert_audit_row")
@patch("services.news_brief.orchestrator.write_brief")
@patch("services.news_brief.orchestrator.compose")
@patch("services.news_brief.orchestrator.curate")
@patch("services.news_brief.orchestrator.parse_email_html")
@patch("services.news_brief.orchestrator.fetch_news_emails")
def test_run_news_brief_no_stories_still_writes(
    fetch_mock: MagicMock,
    parse_mock: MagicMock,
    curate_mock: MagicMock,
    compose_mock: MagicMock,
    write_mock: MagicMock,
    audit_mock: MagicMock,
) -> None:
    """A curator returning zero stories still writes a page and records token data."""
    fetch_mock.return_value = [_fetched()]
    parse_mock.return_value = _parsed()
    curate_mock.return_value = _curate_result([])
    compose_mock.return_value = "No newsworthy stories today."
    write_mock.return_value = "page-empty"

    page_id = run_news_brief(since=SINCE, until=UNTIL)

    assert page_id == "page-empty"
    compose_mock.assert_called_once()
    args, _ = compose_mock.call_args
    assert args[0] == []  # empty stories
    assert len(args[1]) == 1  # but the parsed email is still passed in
    write_mock.assert_called_once()

    audit_values = audit_mock.call_args.args[0]
    assert audit_values["stories_included"] == 0
    assert audit_values["model"] == "claude-sonnet-4-6"  # call was made
    assert audit_values["input_tokens"] == 1000


@patch("services.news_brief.orchestrator._upsert_audit_row")
@patch("services.news_brief.orchestrator.write_brief")
@patch("services.news_brief.orchestrator.compose")
@patch("services.news_brief.orchestrator.curate")
@patch("services.news_brief.orchestrator.parse_email_html")
@patch("services.news_brief.orchestrator.fetch_news_emails")
def test_run_news_brief_writer_failure_propagates(
    fetch_mock: MagicMock,
    parse_mock: MagicMock,
    curate_mock: MagicMock,
    compose_mock: MagicMock,
    write_mock: MagicMock,
    audit_mock: MagicMock,
) -> None:
    fetch_mock.return_value = [_fetched()]
    parse_mock.return_value = _parsed()
    curate_mock.return_value = _curate_result()
    compose_mock.return_value = "..."
    write_mock.side_effect = RuntimeError("notion 500")

    with pytest.raises(RuntimeError, match="notion 500"):
        run_news_brief(since=SINCE, until=UNTIL)

    # Audit row still written with errored=True, carrying the curator data
    # we already have.
    audit_mock.assert_called_once()
    audit_values = audit_mock.call_args.args[0]
    assert audit_values["errored"] is True
    assert audit_values["error_msg"] is not None
    assert "write" in audit_values["error_msg"]
    assert "notion 500" in audit_values["error_msg"]
    assert audit_values["model"] == "claude-sonnet-4-6"  # curator already ran


@patch("services.news_brief.orchestrator._upsert_audit_row")
@patch("services.news_brief.orchestrator.write_brief")
@patch("services.news_brief.orchestrator.compose")
@patch("services.news_brief.orchestrator.curate")
@patch("services.news_brief.orchestrator.parse_email_html")
@patch("services.news_brief.orchestrator.fetch_news_emails")
def test_run_news_brief_curator_failure_emits_curate_stage(
    fetch_mock: MagicMock,
    parse_mock: MagicMock,
    curate_mock: MagicMock,
    compose_mock: MagicMock,
    write_mock: MagicMock,
    audit_mock: MagicMock,
) -> None:
    """A curator failure is attributed to stage=curate, with counts but no token data."""
    fetch_mock.return_value = [_fetched()]
    parse_mock.return_value = _parsed()
    curate_mock.side_effect = RuntimeError("anthropic 429")

    with pytest.raises(RuntimeError, match="anthropic 429"):
        run_news_brief(since=SINCE, until=UNTIL)

    audit_values = audit_mock.call_args.args[0]
    assert audit_values["errored"] is True
    assert "curate" in audit_values["error_msg"]
    assert audit_values["emails_fetched"] == 1
    assert audit_values["stories_considered"] == 1
    assert audit_values["model"] is None  # curator never returned

    compose_mock.assert_not_called()
    write_mock.assert_not_called()


@patch("services.news_brief.orchestrator._upsert_audit_row")
@patch("services.news_brief.orchestrator.curate")
@patch("services.news_brief.orchestrator.parse_email_html")
@patch("services.news_brief.orchestrator.fetch_news_emails")
def test_run_news_brief_fetch_failure_emits_fetch_stage(
    fetch_mock: MagicMock,
    parse_mock: MagicMock,
    curate_mock: MagicMock,
    audit_mock: MagicMock,
) -> None:
    """A failure inside fetch is attributed to stage=fetch."""
    fetch_mock.side_effect = RuntimeError("gmail 503")

    with pytest.raises(RuntimeError, match="gmail 503"):
        run_news_brief(since=SINCE, until=UNTIL)

    audit_values = audit_mock.call_args.args[0]
    assert audit_values["errored"] is True
    assert "fetch" in audit_values["error_msg"]
    assert audit_values["emails_fetched"] == 0

    parse_mock.assert_not_called()
    curate_mock.assert_not_called()


@patch("services.news_brief.orchestrator.write_brief")
@patch("services.news_brief.orchestrator.compose")
@patch("services.news_brief.orchestrator.curate")
@patch("services.news_brief.orchestrator.parse_email_html")
@patch("services.news_brief.orchestrator.fetch_news_emails")
def testrun_with_metrics_returns_counts(
    fetch_mock: MagicMock,
    parse_mock: MagicMock,
    curate_mock: MagicMock,
    compose_mock: MagicMock,
    write_mock: MagicMock,
) -> None:
    fetch_mock.return_value = [_fetched("<m1@x>"), _fetched("<m2@x>"), _fetched("<m3@x>")]
    parse_mock.side_effect = [
        _parsed("<m1@x>"),
        _parsed("<m2@x>"),
        _parsed("<m3@x>"),
    ]
    curate_mock.return_value = _curate_result([_story("<m1@x>"), _story("<m2@x>")])
    compose_mock.return_value = "..."
    write_mock.return_value = "page-xyz"

    result = run_with_metrics(since=SINCE, until=UNTIL)

    assert result.page_id == "page-xyz"
    assert result.email_count == 3
    assert result.story_count == 2
    assert result.duration_s >= 0


@patch("services.news_brief.orchestrator.write_brief")
@patch("services.news_brief.orchestrator.compose")
@patch("services.news_brief.orchestrator.fetch_news_emails")
def test_run_news_brief_until_defaults_to_now(
    fetch_mock: MagicMock,
    compose_mock: MagicMock,
    write_mock: MagicMock,
) -> None:
    """until=None must reach the fetcher as an explicit tz-aware datetime."""
    fetch_mock.return_value = []
    compose_mock.return_value = "..."
    write_mock.return_value = "page-now"

    run_news_brief(since=SINCE)

    fetch_kwargs = fetch_mock.call_args.kwargs
    assert fetch_kwargs["since"] == SINCE
    assert isinstance(fetch_kwargs["until"], datetime)
    assert fetch_kwargs["until"].tzinfo is not None


@patch("services.news_brief.orchestrator._async_upsert_audit_row")
def test_upsert_audit_row_swallows_failures(async_upsert_mock: MagicMock) -> None:
    """_upsert_audit_row must never raise; audit writes are observational.

    Making the inner coroutine raise proves the outer wrapper swallows it.
    """
    from services.news_brief.orchestrator import _upsert_audit_row

    async_upsert_mock.side_effect = RuntimeError("simulated DB outage")
    # Must not raise; the outer wrapper logs at WARNING and swallows.
    _upsert_audit_row({"brief_date": EXPECTED_BRIEF_DATE})
