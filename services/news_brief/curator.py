"""Curate a day's News-labeled emails into structured story candidates.

curate() feeds parsed emails through the Anthropic API with a forced
submit_stories tool and returns the validated StoryCandidate list. The
system prompt and static preamble are cached so daily runs only re-bill
the per-day email payload. Design notes: docs/design.md.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from langsmith import traceable

from services.news_brief.types import CurateResult, ParsedEmail, StoryCandidate
from shared.anthropic_client import get_anthropic_client
from shared.llm_utils import parse_stringified_tool_list

if TYPE_CHECKING:
    from anthropic import Anthropic
    from anthropic.types import ToolParam

DEFAULT_MODEL = "claude-sonnet-4-6"
# Response cap. Forced tool use plus 1-3 sentence summaries means roughly
# 100-150 tokens per story; 16k leaves room for ~80 stories on a heavy day,
# well under Sonnet 4.6's 64k output cap and the SDK's non-streaming-timeout
# guard rail.
DEFAULT_MAX_TOKENS = 16_000

_PROMPT_PATH = Path(__file__).parent / "prompts" / "curator_system.md"

# Static preamble inside the user message. Cached together with the system
# prompt; only the per-day email payload below it changes daily.
_USER_PREAMBLE = """\
Below is today's batch of News-labeled emails to curate. Each email block has:

- `message_id`: the ID you reference in `source_email_id`.
- `sender`: a slug like `nytimes`, `bloomberg`, `theinformation`, etc.
- `subject`: the email subject line.
- `received_at`: ISO-8601 UTC timestamp.
- `plain_text`: body text. `[N]` markers stand in for the links extracted
  from the original HTML — N is the integer you use as `link_id`.
- `links`: a numbered list of the links in this email. Each line is
  `[N] anchor_text` so you can pick the link that best identifies a
  story's source article.

Apply the curation rules from the system prompt and call `submit_stories`
once with all stories you've selected for today's brief.
"""


@lru_cache(maxsize=1)
def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_tool_schema() -> ToolParam:
    """Return the JSON schema for the forced submit_stories tool.

    Mirrors StoryCandidate exactly; the category enum is the source of
    truth for the six valid categories.
    """
    return {
        "name": "submit_stories",
        "description": (
            "Submit the curated stories for today's news brief. Call exactly "
            "once with all selected stories; pass an empty list if today's "
            "emails contain nothing brief-worthy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stories": {
                    "type": "array",
                    "description": "All stories selected for today's brief.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": [
                                    "AI & Tech",
                                    "World & Politics",
                                    "Business & Economy",
                                    "Markets",
                                    "Gadgets & Software",
                                    "Science & Research",
                                ],
                                "description": "Category heading for this story.",
                            },
                            "label": {
                                "type": "string",
                                "description": (
                                    "Bold prefix in the rendered brief. Title-case, "
                                    "no trailing colon."
                                ),
                            },
                            "summary": {
                                "type": "string",
                                "description": (
                                    "1-3 sentences. No em-dashes inside. Don't repeat the label."
                                ),
                            },
                            "source_email_id": {
                                "type": "string",
                                "description": (
                                    "message_id of the ParsedEmail this story was sourced from."
                                ),
                            },
                            "link_id": {
                                "type": "integer",
                                "description": (
                                    "id of the Link in that email's links list "
                                    "that points at the article."
                                ),
                            },
                        },
                        "required": [
                            "category",
                            "label",
                            "summary",
                            "source_email_id",
                            "link_id",
                        ],
                    },
                }
            },
            "required": ["stories"],
        },
    }


def _format_email_block(email: ParsedEmail) -> str:
    links_block = (
        "\n".join(f"[{link.id}] {link.anchor_text}" for link in email.links)
        if email.links
        else "(no links extracted)"
    )
    return (
        f"### Email message_id: {email.message_id}\n"
        f"sender: {email.sender}\n"
        f"subject: {email.subject}\n"
        f"received_at: {email.received_at.isoformat()}\n\n"
        f"plain_text:\n{email.plain_text}\n\n"
        f"links:\n{links_block}\n"
    )


def _format_user_payload(parsed_emails: list[ParsedEmail]) -> str:
    if not parsed_emails:
        return "(no emails received in today's window)"
    return "\n\n---\n\n".join(_format_email_block(e) for e in parsed_emails)


@traceable(run_type="llm", name="news_brief.curate")
def curate(
    parsed_emails: list[ParsedEmail],
    *,
    model: str = DEFAULT_MODEL,
    client: Anthropic | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> CurateResult:
    """Curate today's emails into structured story candidates.

    Returns a CurateResult carrying both the validated stories and the
    Anthropic usage record so the orchestrator can persist token counts and
    cost. An empty stories list means no brief-worthy news today; callers
    decide whether to write an empty-day page or skip.
    """
    api = client or get_anthropic_client()
    system_prompt = _load_system_prompt()
    tool = _build_tool_schema()

    response = api.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_stories"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": _USER_PREAMBLE,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": _format_user_payload(parsed_emails),
                    },
                ],
            }
        ],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_stories":
            payload = cast("dict[str, Any]", block.input)
            stories_raw = payload.get("stories", [])
            # Anthropic occasionally stringifies nested JSON inside tool_use
            # input (or truncates it on max_tokens), so a string is treated
            # as JSON. The composer re-escapes dollars idempotently, so the
            # parser dropping a leaked pre-escape is correctness-preserving.
            if isinstance(stories_raw, str):
                stories_raw = parse_stringified_tool_list(
                    stories_raw, context="curator", stop_reason=response.stop_reason
                )
            stories = [StoryCandidate.model_validate(s) for s in stories_raw]
            return CurateResult(
                stories=stories,
                usage=response.usage,
                model=model,
            )

    raise RuntimeError(
        "curator response missing forced submit_stories tool_use block; "
        f"stop_reason={response.stop_reason!r}"
    )
