"""Tests for the Morning Brief LLM stage (intro + TL;DR + news curation)."""

import json
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from anthropic.types import Usage

from services.morning_brief.classifier import FocusSummary, TaskSections
from services.morning_brief.llm import write_brief_content

TODAY = date(2026, 6, 4)


def _focus() -> FocusSummary:
    return FocusSummary(
        table=(),
        last3_minutes=300,
        last3_daily_avg_min=100.0,
        total14_minutes=1200,
        daily_avg14_min=85.7,
        category_minutes_14d=(("SWE", 600), ("DSA", 400)),
        leetcode_count_7d=5,
        leetcode_categories=("Array", "Graph"),
    )


def _client(tool_input: Any, *, stringify: bool = False) -> MagicMock:
    block = SimpleNamespace(
        type="tool_use",
        name="submit_brief",
        input=json.dumps(tool_input) if stringify else tool_input,
    )
    response = SimpleNamespace(
        content=[block],
        usage=Usage(input_tokens=200, output_tokens=80, cache_read_input_tokens=150),
        stop_reason="tool_use",
    )
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _sample_input() -> dict[str, Any]:
    return {
        "intro": "Good morning. You're heavy on DSA today.",
        "tldr": "Focus on DSA and one mock interview.",
        "news": [
            {
                "category": "AI & Tech",
                "label": "OpenAI",
                "summary": "Raised a large round.",
                "source": "The Information",
                "url": "https://example.com/a",
            },
            {
                "category": "Markets",
                "label": "Fed",
                "summary": "Held rates.",
                "source": "Bloomberg",
                "url": None,
            },
        ],
    }


# News markdown the LLM curates from: the story URL must appear here as a
# markdown link or the parser drops it (citation allowlist).
_NEWS_MD = (
    "## AI & Tech\n> **OpenAI:** Raised a large round. — [The Information](https://example.com/a)\n"
)


def test_write_brief_content_parses_all_fields() -> None:
    client = _client(_sample_input())
    result = write_brief_content(
        today=TODAY,
        focus=_focus(),
        task_sections=TaskSections(),
        news_markdown=_NEWS_MD,
        client=client,
    )
    assert result.intro.startswith("Good morning.")
    assert result.tldr == "Focus on DSA and one mock interview."
    assert len(result.news) == 2
    assert result.news[0].source == "The Information"
    assert result.news[0].url == "https://example.com/a"
    assert result.news[1].url is None  # JSON null becomes None.
    assert result.model == "claude-sonnet-4-6"
    assert result.usage is not None


def test_forced_tool_use_and_cache_control() -> None:
    client = _client(_sample_input())
    write_brief_content(
        today=TODAY,
        focus=_focus(),
        task_sections=TaskSections(),
        news_markdown="",
        client=client,
    )
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "submit_brief"}
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    # The day's payload references the date and the (empty) news fallback.
    payload = kwargs["messages"][0]["content"][0]["text"]
    assert "2026" in payload
    assert "(no News Brief page today)" in payload


def test_stringified_tool_input_is_parsed() -> None:
    client = _client(_sample_input(), stringify=True)
    result = write_brief_content(
        today=TODAY,
        focus=_focus(),
        task_sections=TaskSections(),
        news_markdown="x",
        client=client,
    )
    assert result.tldr == "Focus on DSA and one mock interview."


def test_malformed_news_items_skipped() -> None:
    bad = {
        "intro": "Hi.",
        "tldr": "Day.",
        "news": [
            {"category": "AI & Tech", "label": "X"},  # Missing summary/source.
            "not-a-dict",
            {
                "category": "Markets",
                "label": "Fed",
                "summary": "Held.",
                "source": "Bloomberg",
                "url": "",
            },
        ],
    }
    result = write_brief_content(
        today=TODAY,
        focus=_focus(),
        task_sections=TaskSections(),
        news_markdown="x",
        client=_client(bad),
    )
    assert len(result.news) == 1
    assert result.news[0].label == "Fed"
    assert result.news[0].url is None  # An empty string becomes None.


def test_url_absent_from_news_markdown_dropped() -> None:
    # A URL that never appears in the source markdown is dropped, but the
    # story is kept (it just renders without a hyperlink).
    client = _client(_sample_input())
    result = write_brief_content(
        today=TODAY,
        focus=_focus(),
        task_sections=TaskSections(),
        news_markdown="## AI & Tech\n> OpenAI raised a round. (no links here)",
        client=client,
    )
    assert len(result.news) == 2
    assert result.news[0].label == "OpenAI"
    assert result.news[0].url is None  # A fabricated url absent from source is dropped.


def test_javascript_url_dropped() -> None:
    # Even when the bad-scheme url appears in the source markdown (so it passes
    # the allowlist), the explicit scheme check refuses it: defense in depth.
    payload = _sample_input()
    payload["news"][0]["url"] = "javascript:alert1"
    client = _client(payload)
    result = write_brief_content(
        today=TODAY,
        focus=_focus(),
        task_sections=TaskSections(),
        news_markdown="## AI & Tech\n> OpenAI. — [The Information](javascript:alert1)",
        client=client,
    )
    assert result.news[0].url is None


def test_missing_tool_use_raises() -> None:
    block = SimpleNamespace(type="text", text="no tool call")
    response = SimpleNamespace(content=[block], usage=None, stop_reason="end_turn")
    client = MagicMock()
    client.messages.create.return_value = response

    with pytest.raises(RuntimeError, match="submit_brief"):
        write_brief_content(
            today=TODAY,
            focus=_focus(),
            task_sections=TaskSections(),
            news_markdown="x",
            client=client,
        )
