"""Classify a single inbound email into a primary label plus Flagged overlay.

classify() sends sender, subject, and body to the Anthropic API with a
submit_classification tool schema and returns a validated Classification.
Design notes: docs/design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from langsmith import traceable

from services.triager.labels import PRIMARY_LABELS
from services.triager.types import Classification
from shared.anthropic_client import get_anthropic_client
from shared.llm_utils import parse_stringified_tool_input, truncate_body

if TYPE_CHECKING:
    from anthropic import Anthropic
    from anthropic.types import ToolParam, Usage

DEFAULT_MODEL = "claude-sonnet-4-6"
# Extended-thinking budget (legacy enabled API for Sonnet 4.x). Backtests
# (n=50) averaged ~330 output tokens; Sonnet rarely exhausts the budget,
# but the headroom helps consistency on ambiguous edges.
DEFAULT_THINKING_BUDGET = 8000
# max_tokens must exceed DEFAULT_THINKING_BUDGET because thinking tokens
# count against it. The extra 400 covers the submit_classification tool
# call payload (label + flag + 1-2 sentence reasoning).
DEFAULT_MAX_TOKENS = DEFAULT_THINKING_BUDGET + 400

_PROMPT_PATH = Path(__file__).parent / "prompts" / "classifier_system.md"

# Static preamble inside the user message. Cached together with the system
# prompt; only the per-email payload below it changes per-call.
_USER_PREAMBLE = """\
Below is a single inbound email to classify. Each field is:

- `sender`: the From header value (display name + address).
- `subject`: the email subject line.
- `body_text`: the email body as plain text (HTML stripped). May be
  truncated; if so, a `[... truncated ...]` marker indicates where.

Apply the labeling rules and precedence from the system prompt and call
`submit_classification` exactly once.
"""


@lru_cache(maxsize=1)
def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_tool_schema() -> ToolParam:
    """Build the JSON schema for the submit_classification tool.

    Mirrors Classification exactly; the primary_label enum derives from
    labels.PRIMARY_LABELS so a new label cannot desynchronize the schema.
    """
    return {
        "name": "submit_classification",
        "description": (
            "Submit the classification for this email. Call exactly once with "
            "the primary label, the flagged overlay, and a 1-2 sentence "
            "reasoning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "primary_label": {
                    "type": "string",
                    "enum": list(PRIMARY_LABELS),
                    "description": "The single primary label assigned to this email.",
                },
                "flagged": {
                    "type": "boolean",
                    "description": (
                        "True when the email requires a concrete action "
                        "(reply needed, money at risk, deadline, etc.). "
                        "False for purely informational mail."
                    ),
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "1-2 sentence justification for the label + flag call. "
                        "Plain prose, no markdown, no URLs."
                    ),
                },
            },
            "required": ["primary_label", "flagged", "reasoning"],
        },
    }


def _format_user_payload(*, sender: str, subject: str, body_text: str) -> str:
    return f"sender: {sender}\nsubject: {subject}\n\nbody_text:\n{truncate_body(body_text)}\n"


@dataclass(frozen=True)
class ClassificationResult:
    """A Classification bundled with the Anthropic call's usage and model.

    The runner persists these on the audit row; one return object keeps
    the pieces together.
    """

    classification: Classification
    usage: Usage
    model: str


@traceable(run_type="llm", name="triager.classify")
def classify(
    *,
    sender: str,
    subject: str,
    body_text: str,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> ClassificationResult:
    """Classify a single email into a primary label plus Flagged overlay.

    Returns the Classification plus the call's Usage and model so the
    caller can persist cost data on the audit row. Raises RuntimeError if
    the model fails to emit the submit_classification tool call.
    """
    api = client or get_anthropic_client()
    system_prompt = _load_system_prompt()
    tool = _build_tool_schema()

    # Extended thinking is incompatible with forced tool_choice ("Thinking
    # may not be enabled when tool_choice forces tool use"), so tool choice
    # is auto. submit_classification is the only registered tool and the
    # system prompt instructs the model to call it; the loop below still
    # validates that a tool_use block came back and raises if not.
    #
    # cache_control ttl=1h matches the measured email arrival pattern: ~75%
    # of consecutive emails arrive within an hour, above the 53% break-even
    # for the 1h cache TTL (a 1h cache write costs 2x input vs 1.25x for
    # 5min, but lasts 12x longer). The default 5min TTL is slightly
    # net-negative at the measured 17% within-5-minute arrival rate.
    response = api.messages.create(
        model=model,
        max_tokens=max_tokens,
        thinking={"type": "enabled", "budget_tokens": DEFAULT_THINKING_BUDGET},
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ],
        tools=[tool],
        tool_choice={"type": "auto"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": _USER_PREAMBLE,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    },
                    {
                        "type": "text",
                        "text": _format_user_payload(
                            sender=sender, subject=subject, body_text=body_text
                        ),
                    },
                ],
            }
        ],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_classification":
            payload: Any = block.input
            # Anthropic occasionally stringifies tool input when the model
            # leaks markdown escapes; treat string as JSON.
            if isinstance(payload, str):
                payload = parse_stringified_tool_input(
                    payload, context="classifier", stop_reason=response.stop_reason
                )
            classification = Classification.model_validate(cast("dict[str, Any]", payload))
            return ClassificationResult(
                classification=classification,
                usage=response.usage,
                model=model,
            )

    raise RuntimeError(
        "classifier response missing submit_classification tool_use block; "
        f"stop_reason={response.stop_reason!r}"
    )
