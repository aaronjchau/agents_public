"""Tests for the Notion match cascade.

Anthropic and the ntn subprocess are mocked; subprocess responses route
by data-source id so each query is wired independently.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest

from services.job_apps.notion_match import (
    MatchCascadeResult,
    _build_match_tool_schema,
    _canonicalize_url,
    _extract_urls,
    _format_candidates,
    _validate_llm_output,
    match_email_to_application,
)
from services.job_apps.types import MatchResult, ParsedEmail
from shared.settings import get_settings
from tests.conftest import FakeProc

if TYPE_CHECKING:
    from collections.abc import Callable

# Data-source IDs resolve from the conftest-seeded env; the matcher reads
# them at call time via the cached get_settings().
JOB_APPS_DATA_SOURCE_ID = get_settings().notion_job_apps_data_source_id
COMPANIES_DATA_SOURCE_ID = get_settings().notion_companies_data_source_id


# ---------------------------------------------------------------------- helpers


def _ds_query_url(data_source_id: str) -> str:
    return f"v1/data_sources/{data_source_id}/query"


def _make_subprocess_router(
    responses: dict[str, list[dict[str, Any]]],
    *,
    capture: list[dict[str, Any]] | None = None,
) -> Callable[..., FakeProc]:
    """Return a fake subprocess.run that picks responses by request path."""
    queues: dict[str, list[dict[str, Any]]] = {k: list(v) for k, v in responses.items()}

    def _run(args: list[str], **kwargs: Any) -> FakeProc:
        if capture is not None:
            captured_body: dict[str, Any] = {}
            if len(args) >= 5 and args[3] == "-d":
                captured_body = json.loads(args[4])
            path_in = args[2] if len(args) > 2 else ""
            capture.append({"path": path_in, "args": args, "body": captured_body})
        path = args[2] if len(args) > 2 else ""
        queue = queues.get(path)
        response_obj: dict[str, Any]
        if not queue:
            response_obj = {"results": []}
        elif len(queue) == 1:
            response_obj = queue[0]
        else:
            response_obj = queue.pop(0)
        proc = FakeProc(json.dumps(response_obj))
        proc.args = args
        return proc

    return _run


def _row(
    row_id: str,
    *,
    role: str | None = None,
    company_ids: list[str] | None = None,
    status: str | None = None,
    posting_url: str | None = None,
    title_prop_name: str = "Role Name",
) -> dict[str, Any]:
    """Build a fake Notion row.

    The title property registers with type title because the matcher
    finds it by type; title_prop_name mimics either DB's naming.
    """
    row: dict[str, Any] = {"id": row_id, "object": "page", "properties": {}}
    if role is not None:
        row["properties"][title_prop_name] = {
            "type": "title",
            "title": [{"plain_text": role, "text": {"content": role}}],
        }
    if company_ids is not None:
        row["properties"]["Company"] = {
            "relation": [{"id": cid} for cid in company_ids],
        }
    if status is not None:
        row["properties"]["Status"] = {"select": {"name": status}}
    if posting_url is not None:
        row["properties"]["Posting URL"] = {"url": posting_url}
    return row


def _email(
    *,
    sender: str = "Talent Team <recruiting@acme.com>",
    subject: str = "Re: your application",
    body_text: str = "",
    urls: list[str] | None = None,
) -> ParsedEmail:
    return ParsedEmail(
        sender=sender,
        subject=subject,
        body_text=body_text,
        urls=urls if urls is not None else [],
        received_at_utc=datetime(2026, 5, 5, 14, 0, tzinfo=UTC),
    )


def _llm_response_with_match(outcome: str, row_id: str | None, reasoning: str) -> MagicMock:
    """Build a fake Anthropic response carrying a submit_match tool_use block."""
    response = MagicMock()
    response.stop_reason = "tool_use"
    block = MagicMock()
    block.type = "tool_use"
    block.name = "submit_match"
    block.input = {
        "outcome": outcome,
        "notion_row_id": row_id,
        "reasoning": reasoning,
    }
    response.content = [block]
    usage = MagicMock()
    usage.input_tokens = 200
    usage.output_tokens = 40
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0
    usage.cache_creation = None
    response.usage = usage
    return response


def _make_anthropic_client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = response
    return client


# ---------------------------------------------------------------------- URL helpers


def test_extract_urls_pulls_anchor_and_plain_text() -> None:
    html = (
        '<p>Apply here: <a href="https://boards.greenhouse.io/acme/jobs/123">click</a>'
        " or visit https://acme.com/careers/eng directly.</p>"
    )
    urls = _extract_urls(html)
    assert "https://boards.greenhouse.io/acme/jobs/123" in urls
    assert "https://acme.com/careers/eng" in urls


def test_extract_urls_dedupes() -> None:
    html = '<a href="https://x.com/a">a</a><a href="https://x.com/a">a</a> https://x.com/a'
    urls = _extract_urls(html)
    assert urls.count("https://x.com/a") == 1


def test_canonicalize_url_strips_utm_on_ats_host() -> None:
    raw = "https://boards.greenhouse.io/acme/jobs/123?gh_src=internal&utm_source=email&utm_campaign=apply"
    out = _canonicalize_url(raw)
    assert "utm_source" not in out
    assert "utm_campaign" not in out
    assert "gh_src" not in out
    assert out.startswith("https://boards.greenhouse.io/acme/jobs/123")


def test_canonicalize_url_leaves_non_ats_url_alone() -> None:
    raw = "https://acme.com/careers?utm_source=email"
    assert _canonicalize_url(raw) == raw


# ---------------------------------------------------------------------- step 1: URL match


def test_url_exact_match_returns_matched_without_calling_llm() -> None:
    """Step 1 hit short-circuits the LLM call entirely."""
    posting_url = "https://boards.greenhouse.io/acme/jobs/123"
    parsed = _email(body_text=f'<a href="{posting_url}">apply</a>')
    responses: dict[str, list[dict[str, Any]]] = {
        _ds_query_url(JOB_APPS_DATA_SOURCE_ID): [{"results": [_row("row-1")]}],
    }
    captured: list[dict[str, Any]] = []
    fake = _make_subprocess_router(responses, capture=captured)

    client = MagicMock()
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        cascade = match_email_to_application(parsed_email=parsed, client=client)

    assert isinstance(cascade, MatchCascadeResult)
    assert cascade.result.status == "matched"
    assert cascade.result.notion_row_id == "row-1"
    # URL-match path never invokes Anthropic, so no billing data either.
    assert cascade.usage is None
    assert cascade.model is None
    client.messages.create.assert_not_called()
    # The URL filter was issued exactly once.
    assert len(captured) == 1
    assert captured[0]["body"]["filter"]["property"] == "Posting URL"
    assert captured[0]["body"]["filter"]["url"]["equals"] == posting_url


def test_url_collides_with_two_active_rows_returns_ambiguous() -> None:
    posting_url = "https://jobs.lever.co/acme/abc"
    parsed = _email(body_text=f'<a href="{posting_url}">apply</a>')
    responses: dict[str, list[dict[str, Any]]] = {
        _ds_query_url(JOB_APPS_DATA_SOURCE_ID): [{"results": [_row("row-a"), _row("row-b")]}],
    }
    client = MagicMock()
    with patch(
        "shared.ntn.subprocess.run",
        side_effect=_make_subprocess_router(responses),
    ):
        cascade = match_email_to_application(parsed_email=parsed, client=client)
    assert cascade.result.status == "ambiguous"
    assert cascade.result.notion_row_id is None
    assert {c["id"] for c in cascade.result.candidates} == {"row-a", "row-b"}
    assert cascade.usage is None
    client.messages.create.assert_not_called()


# ---------------------------------------------------------------------- step 2: LLM match


def test_no_url_invokes_llm_with_filtered_candidates() -> None:
    """With no URL hit the active candidates are fetched and handed to the LLM."""
    parsed = _email(
        sender="Talent <hiring@acme.com>",
        subject="Phone screen for Backend Engineer",
        body_text="Hi Alex, let's set up a 30-minute call.",
    )
    responses: dict[str, list[dict[str, Any]]] = {
        _ds_query_url(JOB_APPS_DATA_SOURCE_ID): [
            {
                "results": [
                    _row(
                        "app-1",
                        role="Backend Engineer",
                        company_ids=["co-1"],
                        status="Applied",
                    )
                ]
            }
        ],
        _ds_query_url(COMPANIES_DATA_SOURCE_ID): [{"results": [_row("co-1", role="Acme")]}],
    }
    captured: list[dict[str, Any]] = []
    fake = _make_subprocess_router(responses, capture=captured)

    client = _make_anthropic_client(
        _llm_response_with_match("matched", "app-1", "Acme + Backend Engineer matches.")
    )
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        cascade = match_email_to_application(parsed_email=parsed, client=client)

    assert cascade.result.status == "matched"
    assert cascade.result.notion_row_id == "app-1"
    # LLM did fire on this path, so usage + model are populated.
    assert cascade.usage is not None
    assert cascade.usage.input_tokens == 200
    assert cascade.model == "claude-opus-4-7"

    # The candidate fetch filters only on Application Date is_not_empty;
    # terminal-status rows stay in the candidate set so mail about
    # closed applications still matches, and the writer's terminal guard
    # keeps that safe.
    job_apps_calls = [c for c in captured if c["path"] == _ds_query_url(JOB_APPS_DATA_SOURCE_ID)]
    assert len(job_apps_calls) == 1
    filter_obj = job_apps_calls[0]["body"]["filter"]
    assert filter_obj == {
        "property": "Application Date",
        "date": {"is_not_empty": True},
    }

    # The LLM was called exactly once, with the candidate row in the user message.
    client.messages.create.assert_called_once()
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-opus-4-7"
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["extra_body"] == {"output_config": {"effort": "high"}}
    user_text = kwargs["messages"][0]["content"]
    assert "row_id='app-1'" in user_text
    assert "company='Acme'" in user_text
    assert "role='Backend Engineer'" in user_text


def test_no_url_no_active_candidates_returns_no_match() -> None:
    """An empty candidate set returns no_match without calling the LLM."""
    parsed = _email(sender="x@unknownco.com")
    responses: dict[str, list[dict[str, Any]]] = {
        _ds_query_url(JOB_APPS_DATA_SOURCE_ID): [{"results": []}],
    }
    client = MagicMock()
    with patch(
        "shared.ntn.subprocess.run",
        side_effect=_make_subprocess_router(responses),
    ):
        cascade = match_email_to_application(parsed_email=parsed, client=client)
    assert cascade.result.status == "no_match"
    # Empty-candidates short-circuit skips the LLM entirely.
    assert cascade.usage is None
    client.messages.create.assert_not_called()


def test_llm_returns_no_match_passes_through() -> None:
    parsed = _email(sender="x@unrelated.com")
    responses: dict[str, list[dict[str, Any]]] = {
        _ds_query_url(JOB_APPS_DATA_SOURCE_ID): [
            {
                "results": [
                    _row("app-1", role="Backend Engineer", company_ids=["co-1"], status="Applied")
                ]
            }
        ],
        _ds_query_url(COMPANIES_DATA_SOURCE_ID): [{"results": [_row("co-1", role="Acme")]}],
    }
    client = _make_anthropic_client(
        _llm_response_with_match("no_match", None, "Sender unrelated to candidates.")
    )
    with patch(
        "shared.ntn.subprocess.run",
        side_effect=_make_subprocess_router(responses),
    ):
        cascade = match_email_to_application(parsed_email=parsed, client=client)
    assert cascade.result.status == "no_match"
    assert cascade.result.notion_row_id is None
    assert cascade.usage is not None


def test_llm_returns_ambiguous_passes_through() -> None:
    parsed = _email(sender="recruiting@acme.com", subject="Following up")
    responses: dict[str, list[dict[str, Any]]] = {
        _ds_query_url(JOB_APPS_DATA_SOURCE_ID): [
            {
                "results": [
                    _row("app-be", role="Backend Engineer", company_ids=["co-1"], status="Applied"),
                    _row(
                        "app-fs", role="Full-Stack Engineer", company_ids=["co-1"], status="Applied"
                    ),
                ]
            }
        ],
        _ds_query_url(COMPANIES_DATA_SOURCE_ID): [{"results": [_row("co-1", role="Acme")]}],
    }
    client = _make_anthropic_client(
        _llm_response_with_match("ambiguous", None, "Two roles plausibly match; can't pick.")
    )
    with patch(
        "shared.ntn.subprocess.run",
        side_effect=_make_subprocess_router(responses),
    ):
        cascade = match_email_to_application(parsed_email=parsed, client=client)
    assert cascade.result.status == "ambiguous"
    assert cascade.result.notion_row_id is None
    assert cascade.usage is not None


def test_llm_picking_unknown_row_id_degrades_to_ambiguous() -> None:
    """A hallucinated row_id outside the candidate set degrades to ambiguous."""
    parsed = _email(sender="recruiting@acme.com")
    responses: dict[str, list[dict[str, Any]]] = {
        _ds_query_url(JOB_APPS_DATA_SOURCE_ID): [
            {
                "results": [
                    _row("app-1", role="Backend Engineer", company_ids=["co-1"], status="Applied")
                ]
            }
        ],
        _ds_query_url(COMPANIES_DATA_SOURCE_ID): [{"results": [_row("co-1", role="Acme")]}],
    }
    client = _make_anthropic_client(
        _llm_response_with_match("matched", "ghost-id-not-in-candidates", "I made it up.")
    )
    with patch(
        "shared.ntn.subprocess.run",
        side_effect=_make_subprocess_router(responses),
    ):
        cascade = match_email_to_application(parsed_email=parsed, client=client)
    assert cascade.result.status == "ambiguous"
    assert cascade.result.notion_row_id is None


def test_llm_response_missing_tool_use_raises() -> None:
    parsed = _email(sender="recruiting@acme.com")
    responses: dict[str, list[dict[str, Any]]] = {
        _ds_query_url(JOB_APPS_DATA_SOURCE_ID): [
            {
                "results": [
                    _row("app-1", role="Backend Engineer", company_ids=["co-1"], status="Applied")
                ]
            }
        ],
        _ds_query_url(COMPANIES_DATA_SOURCE_ID): [{"results": [_row("co-1", role="Acme")]}],
    }
    response = MagicMock()
    response.stop_reason = "end_turn"
    text_block = MagicMock()
    text_block.type = "text"
    response.content = [text_block]
    client = _make_anthropic_client(response)
    with (
        patch(
            "shared.ntn.subprocess.run",
            side_effect=_make_subprocess_router(responses),
        ),
        pytest.raises(RuntimeError, match="missing submit_match tool_use"),
    ):
        match_email_to_application(parsed_email=parsed, client=client)


# ---------------------------------------------------------------------- helper-level units


def test_format_candidates_emits_compact_one_line_per_row() -> None:
    candidates: list[dict[str, Any]] = [
        {
            "row_id": "app-1",
            "company": "Acme",
            "role": "Backend",
            "status": "Applied",
            "posting_url": "https://acme.com/jobs/1",
        },
        {
            "row_id": "app-2",
            "company": "Beta",
            "role": "Frontend",
            "status": "Screen",
            "posting_url": None,
        },
    ]
    out = _format_candidates(candidates)
    lines = out.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("1. row_id='app-1'")
    assert "company='Acme'" in lines[0]
    assert "posting_url='https://acme.com/jobs/1'" in lines[0]
    assert "posting_url='null'" in lines[1]


def test_validate_llm_output_matched_with_known_row() -> None:
    candidates = [{"row_id": "app-1", "company": "Acme"}]
    result = _validate_llm_output(
        {"outcome": "matched", "notion_row_id": "app-1", "reasoning": "x"},
        valid_row_ids={"app-1"},
        candidates=candidates,
    )
    assert isinstance(result, MatchResult)
    assert result.status == "matched"
    assert result.notion_row_id == "app-1"


def test_validate_llm_output_matched_with_unknown_row_degrades() -> None:
    result = _validate_llm_output(
        {"outcome": "matched", "notion_row_id": "ghost", "reasoning": "x"},
        valid_row_ids={"app-1"},
        candidates=[{"row_id": "app-1"}],
    )
    assert result.status == "ambiguous"


def test_validate_llm_output_ambiguous_passes() -> None:
    result = _validate_llm_output(
        {"outcome": "ambiguous", "notion_row_id": None, "reasoning": "x"},
        valid_row_ids=set(),
        candidates=[],
    )
    assert result.status == "ambiguous"


def test_validate_llm_output_no_match_passes() -> None:
    result = _validate_llm_output(
        {"outcome": "no_match", "notion_row_id": None, "reasoning": "x"},
        valid_row_ids=set(),
        candidates=[],
    )
    assert result.status == "no_match"


def test_match_tool_schema_shape() -> None:
    schema = _build_match_tool_schema()
    assert schema["name"] == "submit_match"
    input_schema = cast("dict[str, Any]", schema["input_schema"])
    props = cast("dict[str, Any]", input_schema["properties"])
    assert set(props["outcome"]["enum"]) == {"matched", "ambiguous", "no_match"}
    required = cast("list[str]", input_schema["required"])
    assert set(required) == {"outcome", "notion_row_id", "reasoning"}
