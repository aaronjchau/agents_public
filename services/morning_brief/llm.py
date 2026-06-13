"""The Morning Brief's single LLM call: intro, TL;DR, and news curation.

One forced-tool-use Anthropic request returns all three via the
submit_brief tool; everything deterministic is built elsewhere. The system
prompt and tool schema are cached so only the per-day payload re-bills.
Design notes: docs/design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from langsmith import traceable

from services.morning_brief.composer import format_hours
from services.morning_brief.types import NewsStory
from shared.anthropic_client import get_anthropic_client
from shared.llm_utils import parse_stringified_tool_input

if TYPE_CHECKING:
    from datetime import date

    from anthropic import Anthropic
    from anthropic.types import ToolParam, Usage

    from services.morning_brief.classifier import FocusSummary, TaskSections

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 8000
_PROMPT_PATH = Path(__file__).parent / "prompts" / "brief_writer_system.md"
_NEWS_TRUNCATE_CHARS = 16000
# Capture the target of a markdown link "](...)"; [^)]* stops at the first
# ')', which is the link boundary in the News Brief's single-line citations.
_MD_LINK_TARGET = re.compile(r"\]\(([^)]*)\)")


@dataclass(frozen=True)
class BriefContent:
    """The LLM-produced parts of the brief, plus billing data."""

    intro: str
    tldr: str
    news: list[NewsStory]
    usage: Usage | None
    model: str | None


@traceable(run_type="llm", name="morning_brief.write")
def write_brief_content(
    *,
    today: date,
    focus: FocusSummary,
    task_sections: TaskSections,
    news_markdown: str,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> BriefContent:
    """Generate the intro, TL;DR, and curated news for the brief."""
    api = client or get_anthropic_client()
    response = api.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": _load_prompt(), "cache_control": {"type": "ephemeral"}}],
        tools=[_tool_schema()],
        tool_choice={"type": "tool", "name": "submit_brief"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": _format_payload(today, focus, task_sections, news_markdown),
                        "type": "text",
                    }
                ],
            }
        ],
    )
    return _parse_response(response, model, _link_allowlist(news_markdown))


@lru_cache(maxsize=1)
def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _tool_schema() -> ToolParam:
    return {
        "name": "submit_brief",
        "description": "Submit the brief's intro, TL;DR, and curated Major News. Call exactly once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "intro": {
                    "type": "string",
                    "description": "2-3 sentence Good Morning intro, no heading.",
                },
                "tldr": {"type": "string", "description": "1-2 sentence TL;DR summary."},
                "news": {
                    "type": "array",
                    "description": "Curated Major News stories; empty if no news.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "label": {"type": "string"},
                            "summary": {"type": "string"},
                            "source": {"type": "string"},
                            "url": {"type": ["string", "null"]},
                        },
                        "required": ["category", "label", "summary", "source", "url"],
                    },
                },
            },
            "required": ["intro", "tldr", "news"],
        },
    }


def _format_payload(
    today: date,
    focus: FocusSummary,
    task_sections: TaskSections,
    news_markdown: str,
) -> str:
    category_minutes = (
        ", ".join(f"{name} {format_hours(minutes)}" for name, minutes in focus.category_minutes_14d)
        or "(none logged)"
    )
    leetcode_categories = ", ".join(focus.leetcode_categories) or "n/a"
    today_lines = (
        "\n".join(f"  - {ct.task.name} [{ct.project.name}]" for ct in task_sections.today)
        or "  (none)"
    )
    news_block = news_markdown.strip()[:_NEWS_TRUNCATE_CHARS] or "(no News Brief page today)"

    return (
        f"DATE: {today.strftime('%A, %B %-d, %Y')}\n\n"
        "FOCUS:\n"
        f"- last 3 days: {format_hours(focus.last3_minutes)} "
        f"(~{format_hours(round(focus.last3_daily_avg_min))}/day)\n"
        f"- 14-day total: {format_hours(focus.total14_minutes)} "
        f"(~{format_hours(round(focus.daily_avg14_min))}/day)\n"
        f"- minutes by category (14d): {category_minutes}\n"
        f"- LeetCode (7d): {focus.leetcode_count_7d} problems ({leetcode_categories})\n\n"
        "TASKS:\n"
        f"- Today ({len(task_sections.today)}):\n{today_lines}\n"
        f"- This Week: {len(task_sections.this_week)}\n"
        f"- Overdue: {len(task_sections.overdue)}\n"
        f"- To reschedule: {len(task_sections.reschedule)}\n\n"
        "NEWS BRIEF (today's page markdown; curate the highlights):\n"
        f"{news_block}\n"
    )


def _link_allowlist(news_markdown: str) -> set[str]:
    """Return the URLs the LLM may cite: the markdown-link targets in the source.

    The model free-types each story's url from untrusted news markdown, so
    a citation is only honored if its target actually appears as a link in
    that source; otherwise it could render an arbitrary (e.g. phishing)
    hyperlink.
    """
    return set(_MD_LINK_TARGET.findall(news_markdown))


def _parse_response(response: Any, model: str, allowed_urls: set[str]) -> BriefContent:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_brief":
            payload: Any = block.input
            if isinstance(payload, str):
                payload = parse_stringified_tool_input(
                    payload,
                    context="brief writer",
                    stop_reason=getattr(response, "stop_reason", None),
                )
            data = cast("dict[str, Any]", payload)
            return BriefContent(
                intro=str(data.get("intro") or "").strip(),
                tldr=str(data.get("tldr") or "").strip(),
                news=_parse_news(data.get("news"), allowed_urls),
                usage=getattr(response, "usage", None),
                model=model,
            )
    raise RuntimeError(
        f"brief writer response missing submit_brief tool_use; stop_reason="
        f"{getattr(response, 'stop_reason', None)!r}"
    )


def _parse_news(raw: Any, allowed_urls: set[str]) -> list[NewsStory]:
    if not isinstance(raw, list):
        return []
    stories: list[NewsStory] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        category = item.get("category")
        label = item.get("label")
        summary = item.get("summary")
        source = item.get("source")
        if not (
            isinstance(category, str)
            and category
            and isinstance(label, str)
            and label
            and isinstance(summary, str)
            and summary
            and isinstance(source, str)
            and source
        ):
            continue
        stories.append(
            NewsStory(
                category=category,
                label=label,
                summary=summary,
                source=source,
                url=_safe_url(item.get("url"), allowed_urls),
            )
        )
    return stories


def _safe_url(url: Any, allowed_urls: set[str]) -> str | None:
    """Keep a story's url only if it appears in the source and is http(s).

    The scheme check is redundant with the allowlist (the source's own links
    are http(s)) but kept explicit so the guarantee doesn't depend on it.
    """
    if not (isinstance(url, str) and url and url in allowed_urls):
        return None
    if not url.startswith(("http://", "https://")):
        return None
    return url
