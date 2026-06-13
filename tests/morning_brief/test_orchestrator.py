"""Tests for the Morning Brief orchestrator."""

import contextlib
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from anthropic.types import Usage

from services.morning_brief import orchestrator
from services.morning_brief.llm import BriefContent
from services.morning_brief.types import NewsBriefPage

BRIEF_DATE = date(2026, 6, 4)


def _content() -> BriefContent:
    return BriefContent(
        intro="Good morning.",
        tldr="Light day.",
        news=[],
        usage=Usage(input_tokens=100, output_tokens=40, cache_read_input_tokens=10),
        model="claude-sonnet-4-6",
    )


@contextlib.contextmanager
def _patched(**overrides: Any) -> Iterator[dict[str, MagicMock]]:
    """Patch every orchestrator dependency; yield the mocks by name.

    Defaults: empty fetches, a stub LLM result, a successful write. An
    override replaces a target's return value; an Exception instance
    becomes its side_effect.
    """
    targets: dict[str, Any] = {
        "fetch_tasks": [],
        "fetch_focus_entries": [],
        "fetch_leetcode": [],
        "fetch_news_page": None,
        "find_existing_brief": None,
        "fetch_emails": [],
        "fetch_events": [],
        "fetch_holidays": [],
        "write_brief_content": _content(),
        "write_brief": "page-1",
        "_upsert_audit_row": None,
    }
    targets.update(overrides)
    with contextlib.ExitStack() as stack:
        mocks: dict[str, MagicMock] = {}
        for name, value in targets.items():
            kwargs = (
                {"side_effect": value} if isinstance(value, Exception) else {"return_value": value}
            )
            mocks[name] = stack.enter_context(
                patch(f"services.morning_brief.orchestrator.{name}", **kwargs)
            )
        yield mocks


def test_happy_path_writes_and_audits() -> None:
    with _patched() as mocks:
        result = orchestrator.run_with_metrics(today=BRIEF_DATE)

    assert result.page_id == "page-1"
    # Write got an existing_page_id of None (no dedup hit) and a markdown body.
    write = mocks["write_brief"]
    assert write.call_args.kwargs["existing_page_id"] is None
    assert write.call_args.kwargs["markdown_body"]
    values = mocks["_upsert_audit_row"].call_args.args[0]
    assert values["brief_date"] == BRIEF_DATE
    assert values["errored"] is False
    assert values["notion_page_id"] == "page-1"
    assert values["model"] == "claude-sonnet-4-6"
    assert isinstance(values["cost_usd"], Decimal)


def test_news_markdown_passed_to_llm() -> None:
    with _patched(fetch_news_page=NewsBriefPage("news-1", "## AI & Tech\n> story")) as mocks:
        orchestrator.run_with_metrics(today=BRIEF_DATE)

    llm = mocks["write_brief_content"]
    assert llm.call_args.kwargs["news_markdown"] == "## AI & Tech\n> story"


def test_existing_brief_id_threads_to_writer() -> None:
    with _patched(find_existing_brief="dup-1", write_brief="dup-1") as mocks:
        orchestrator.run_with_metrics(today=BRIEF_DATE)

    assert mocks["write_brief"].call_args.kwargs["existing_page_id"] == "dup-1"


def test_llm_failure_falls_back_and_still_writes() -> None:
    with _patched(write_brief_content=RuntimeError("llm down"), write_brief="p") as mocks:
        result = orchestrator.run_with_metrics(today=BRIEF_DATE)

    assert result.page_id == "p"
    # Fallback intro shipped; no LLM cost recorded.
    assert "Good morning." in mocks["write_brief"].call_args.kwargs["markdown_body"]
    assert mocks["_upsert_audit_row"].call_args.args[0]["model"] is None


def test_fetch_failure_is_isolated() -> None:
    with _patched(fetch_tasks=RuntimeError("notion down")) as mocks:
        result = orchestrator.run_with_metrics(today=BRIEF_DATE)

    # Tasks fetch failed but the brief still published with zero tasks.
    assert result.tasks_today == 0
    assert mocks["_upsert_audit_row"].call_args.args[0]["errored"] is False


def test_dedup_lookup_failure_fails_the_run() -> None:
    # find_existing_brief is deliberately not guarded: swallowing a failure
    # would create a duplicate page. The run must fail and audit errored.
    with (
        _patched(find_existing_brief=RuntimeError("notion 429")) as mocks,
        pytest.raises(RuntimeError),
    ):
        orchestrator.run_with_metrics(today=BRIEF_DATE)

    mocks["write_brief"].assert_not_called()
    assert mocks["_upsert_audit_row"].call_args.args[0]["errored"] is True


def test_write_failure_audits_errored_and_raises() -> None:
    with (
        _patched(write_brief=RuntimeError("notion 500")) as mocks,
        pytest.raises(RuntimeError),
    ):
        orchestrator.run_with_metrics(today=BRIEF_DATE)

    values = mocks["_upsert_audit_row"].call_args.args[0]
    assert values["errored"] is True
    assert "RuntimeError" in values["error_msg"]
