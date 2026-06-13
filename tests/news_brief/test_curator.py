"""Tests for the news-brief curator with a mocked Anthropic client.

A separate opt-in test (AGENTS_LIVE_LLM=1) runs the real model against
local fixtures from NEWS_BRIEF_SAMPLE_DIR and prints the stories.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, cast

import pytest
from anthropic.types import Message, TextBlock, Usage

from services.news_brief.curator import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    _build_tool_schema,
    curate,
)
from services.news_brief.parser import parse_email_html
from services.news_brief.types import CurateResult, Link, ParsedEmail, StoryCandidate
from tests.helpers import make_mock_client, make_mock_response

SAMPLE_DIR = Path(os.environ.get("NEWS_BRIEF_SAMPLE_DIR", "/tmp/news-brief-samples"))


def _make_email(
    *,
    message_id: str = "<msg-1@example.com>",
    sender: str = "theinformation",
    subject: str = "Anthropic raises round",
    plain_text: str = "Anthropic closed a [1] funding round at a record valuation.",
    links: list[Link] | None = None,
) -> ParsedEmail:
    return ParsedEmail(
        message_id=message_id,
        sender=sender,
        subject=subject,
        received_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
        plain_text=plain_text,
        links=links or [Link(id=1, url="https://example.com/a", anchor_text="funding")],
    )


def _make_mock_response(stories: list[dict[str, Any]] | str) -> Message:
    """Build a Message with one submit_stories tool_use block.

    A str value exercises the stringified-stories defensive parse path.
    """
    return make_mock_response(
        tool_name="submit_stories",
        input_payload={"stories": stories},
        model=DEFAULT_MODEL,
    )


def test_curate_returns_validated_story_candidates() -> None:
    stories = [
        {
            "category": "AI & Tech",
            "label": "Anthropic",
            "summary": "Closed a record funding round at \\$300B valuation.",
            "source_email_id": "<msg-1@example.com>",
            "link_id": 1,
        },
    ]
    client = make_mock_client(_make_mock_response(stories))

    result = curate([_make_email()], client=client)

    assert isinstance(result, CurateResult)
    assert len(result.stories) == 1
    story = result.stories[0]
    assert isinstance(story, StoryCandidate)
    assert story.category == "AI & Tech"
    assert story.label == "Anthropic"
    assert story.source_email_id == "<msg-1@example.com>"
    assert story.link_id == 1
    # Usage record carried through for the orchestrator to persist.
    assert result.model == DEFAULT_MODEL
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 10


def test_curate_parses_stringified_stories_field() -> None:
    """A JSON-stringified stories array routes through the shared defensive parser."""
    raw_stories = (
        '[{"category": "Markets", "label": "Fed", '
        '"summary": "Held rates; futures price in a \\$2T shift.", '
        '"source_email_id": "<msg-1@example.com>", "link_id": 1}]'
    )
    client = make_mock_client(_make_mock_response(raw_stories))

    result = curate([_make_email()], client=client)

    assert len(result.stories) == 1
    assert result.stories[0].label == "Fed"
    # The invalid pre-escape was stripped to a bare dollar sign.
    assert "$2T" in result.stories[0].summary


def test_curate_empty_stories_returns_empty_list() -> None:
    client = make_mock_client(_make_mock_response(stories=[]))
    result = curate([_make_email()], client=client)
    assert isinstance(result, CurateResult)
    assert result.stories == []


def test_curate_uses_default_model_and_max_tokens() -> None:
    client = make_mock_client(_make_mock_response(stories=[]))
    curate([_make_email()], client=client)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == DEFAULT_MODEL
    assert kwargs["max_tokens"] == DEFAULT_MAX_TOKENS


def test_curate_respects_model_override() -> None:
    client = make_mock_client(_make_mock_response(stories=[]))
    curate([_make_email()], model="claude-haiku-4-5", client=client)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5"


def test_curate_caches_system_prompt() -> None:
    client = make_mock_client(_make_mock_response(stories=[]))
    curate([_make_email()], client=client)

    kwargs = client.messages.create.call_args.kwargs
    system = kwargs["system"]
    assert isinstance(system, list)
    assert len(system) == 1
    block = system[0]
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}
    # Sanity-check the prompt is non-empty and load-bearing.
    assert "submit_stories" in block["text"]
    assert "AI & Tech" in block["text"]


def test_curate_caches_user_preamble_but_not_email_payload() -> None:
    client = make_mock_client(_make_mock_response(stories=[]))
    curate([_make_email()], client=client)

    kwargs = client.messages.create.call_args.kwargs
    messages = kwargs["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"

    content = messages[0]["content"]
    assert isinstance(content, list)
    assert len(content) == 2

    preamble, payload = content
    assert preamble["type"] == "text"
    assert preamble["cache_control"] == {"type": "ephemeral"}
    assert payload["type"] == "text"
    # The per-day payload must not be cached; it changes daily.
    assert "cache_control" not in payload


def test_curate_forces_submit_stories_tool() -> None:
    client = make_mock_client(_make_mock_response(stories=[]))
    curate([_make_email()], client=client)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "submit_stories"}


def test_curate_tool_schema_matches_story_candidate_shape() -> None:
    client = make_mock_client(_make_mock_response(stories=[]))
    curate([_make_email()], client=client)

    kwargs = client.messages.create.call_args.kwargs
    tools = kwargs["tools"]
    assert len(tools) == 1
    schema = tools[0]
    assert schema["name"] == "submit_stories"

    item_props = schema["input_schema"]["properties"]["stories"]["items"]["properties"]
    expected = {"category", "label", "summary", "source_email_id", "link_id"}
    assert set(item_props.keys()) == expected
    assert item_props["link_id"]["type"] == "integer"
    assert item_props["source_email_id"]["type"] == "string"
    # Categories must match the curator's source of truth.
    assert set(item_props["category"]["enum"]) == {
        "AI & Tech",
        "World & Politics",
        "Business & Economy",
        "Markets",
        "Gadgets & Software",
        "Science & Research",
    }


def test_curate_user_message_contains_per_email_fields() -> None:
    email = _make_email(
        message_id="<unique-test-id-99@example.com>",
        sender="bloomberg",
        subject="Bloomberg Evening Briefing",
        plain_text="The Fed cut [1] rates by 25bps today.",
        links=[
            Link(
                id=1,
                url="https://www.bloomberg.com/article",
                anchor_text="rates by 25bps",
            )
        ],
    )
    client = make_mock_client(_make_mock_response(stories=[]))
    curate([email], client=client)

    kwargs = client.messages.create.call_args.kwargs
    payload_text = kwargs["messages"][0]["content"][1]["text"]
    assert "<unique-test-id-99@example.com>" in payload_text
    assert "bloomberg" in payload_text
    assert "Bloomberg Evening Briefing" in payload_text
    assert "[1] rates by 25bps" in payload_text
    assert "The Fed cut" in payload_text


def test_curate_handles_no_emails() -> None:
    client = make_mock_client(_make_mock_response(stories=[]))
    result = curate([], client=client)
    assert result.stories == []

    kwargs = client.messages.create.call_args.kwargs
    payload_text = kwargs["messages"][0]["content"][1]["text"]
    assert "no emails received" in payload_text.lower()


def test_curate_raises_when_response_missing_tool_use() -> None:
    """A text-only response despite forced tool use raises instead of returning []."""
    text_only = Message(
        id="msg_test_02",
        type="message",
        role="assistant",
        model=DEFAULT_MODEL,
        content=[TextBlock(type="text", text="I refuse.", citations=None)],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(input_tokens=5, output_tokens=5),
    )
    client = make_mock_client(text_only)

    with pytest.raises(RuntimeError, match="submit_stories"):
        curate([_make_email()], client=client)


def test_tool_schema_top_level_required_includes_stories() -> None:
    schema = _build_tool_schema()
    input_schema = cast("dict[str, Any]", schema["input_schema"])
    assert input_schema["required"] == ["stories"]
    assert input_schema["properties"]["stories"]["type"] == "array"


# ---------------------------------------------------------------------------
# Live-API test (opt-in via AGENTS_LIVE_LLM=1)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AGENTS_LIVE_LLM") != "1",
    reason="set AGENTS_LIVE_LLM=1 to hit the real Anthropic API",
)
@pytest.mark.skipif(
    not SAMPLE_DIR.is_dir(),
    reason=f"{SAMPLE_DIR} not populated; cannot run live curator test",
)
def test_curate_live_against_real_samples() -> None:
    """Run the curator against real sample emails and print the results.

    Caps the input at 10 fixtures to keep cost predictable. Asserts only
    that the call succeeds and every story's ids resolve back to the input.
    """
    html_files = sorted(SAMPLE_DIR.rglob("*.html"))[:10]
    if not html_files:
        pytest.skip(f"{SAMPLE_DIR} has no *.html fixtures yet")

    parsed_emails: list[ParsedEmail] = []
    for html_path in html_files:
        meta = json.loads(html_path.with_suffix(".json").read_text())
        # Fetcher writes received_at_utc_iso (RFC 2822); sender slug is the
        # parent dir, since meta["sender"] is the full From header.
        parsed_emails.append(
            parse_email_html(
                html_path.read_text(),
                message_id=meta["message_id"],
                sender=html_path.parent.name,
                subject=meta["subject"],
                received_at=parsedate_to_datetime(meta["received_at_utc_iso"]),
            )
        )

    result = curate(parsed_emails)
    stories = result.stories

    valid_email_ids = {email.message_id for email in parsed_emails}
    valid_link_ids: dict[str, set[int]] = {
        email.message_id: {link.id for link in email.links} for email in parsed_emails
    }
    for story in stories:
        assert story.source_email_id in valid_email_ids, (
            f"story references unknown email_id={story.source_email_id!r}"
        )
        assert story.link_id in valid_link_ids[story.source_email_id], (
            f"story references unknown link_id={story.link_id} in email {story.source_email_id!r}"
        )

    print(f"\n[live] {len(stories)} stories from {len(parsed_emails)} emails:")
    for s in stories:
        print(f"  - [{s.category}] {s.label}: {s.summary[:80]}")
    print(
        f"[live] usage: input={result.usage.input_tokens} output={result.usage.output_tokens} "
        f"cache_read={result.usage.cache_read_input_tokens or 0}"
    )
