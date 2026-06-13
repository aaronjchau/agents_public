"""Classify a Job-Apps-labeled email into one of seven sublabels.

classify_sublabel feeds sender, subject, and body through the Anthropic
API with a submit_sublabel tool schema and returns a validated
classification. Design notes: docs/design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from langsmith import traceable

from services.job_apps.types import JobAppsClassification
from shared.anthropic_client import get_anthropic_client
from shared.llm_utils import parse_stringified_tool_input, truncate_body

if TYPE_CHECKING:
    from anthropic import Anthropic
    from anthropic.types import ToolParam, Usage

DEFAULT_MODEL = "claude-opus-4-7"
# "high" is the most thorough non-streaming effort; "xhigh" requires
# streaming due to the >10min API timeout, and sublabel disambiguation
# is bounded reasoning that does not need it.
DEFAULT_EFFORT = "high"
# Adaptive thinking decides its own depth, so max_tokens is a ceiling
# rather than a budget; 16k leaves room to think and emit the tool call
# without truncation.
DEFAULT_MAX_TOKENS = 16000

_PROMPT_PATH = Path(__file__).parent / "prompts" / "sublabel_classifier.md"

# Static preamble kept as its own block so the per-email payload stays a
# clean second block. Not cached; see docs/design.md.
_USER_PREAMBLE = """\
Below is a single Job-Apps-labeled email to assign a sublabel for. Each
field is:

- `sender`: the From header value (display name + address).
- `subject`: the email subject line.
- `body_text`: the email body as plain text (HTML stripped). May be
  truncated; if so, a `[... truncated ...]` marker indicates where.

Apply the sublabel rules and disambiguation order from the system prompt
and call `submit_sublabel` exactly once.
"""

_SUBLABEL_ENUM = [
    "Offer",
    "Interview Scheduling",
    "Assessment",
    "Recruiter Outreach",
    "Status Update",
    "Application Confirmation",
    "Rejection",
]


@lru_cache(maxsize=1)
def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_tool_schema() -> ToolParam:
    """JSON schema for the submit_sublabel tool.

    Mirrors JobAppsClassification; the sublabel enum is the source of
    truth for the seven valid sublabels.
    """
    return {
        "name": "submit_sublabel",
        "description": (
            "Submit the sublabel for this Job-Apps-labeled email. Call "
            "exactly once with the chosen sublabel and a 1-2 sentence "
            "reasoning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sublabel": {
                    "type": "string",
                    "enum": _SUBLABEL_ENUM,
                    "description": "The single sublabel assigned to this email.",
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "1-2 sentence justification for the sublabel call. "
                        "Plain prose, no markdown, no URLs."
                    ),
                },
            },
            "required": ["sublabel", "reasoning"],
        },
    }


def _format_user_payload(*, sender: str, subject: str, body_text: str) -> str:
    return f"sender: {sender}\nsubject: {subject}\n\nbody_text:\n{truncate_body(body_text)}\n"


@dataclass(frozen=True)
class SublabelClassificationResult:
    """A JobAppsClassification plus the call's usage and model.

    Bundling keeps the graph layer from accidentally dropping the
    billing data the runner aggregates into the audit row.
    """

    classification: JobAppsClassification
    usage: Usage
    model: str


@traceable(run_type="llm", name="job_apps.classify_sublabel")
def classify_sublabel(
    *,
    sender: str,
    subject: str,
    body_text: str,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> SublabelClassificationResult:
    """Classify a Job-Apps-labeled email into one of seven sublabels.

    Raises RuntimeError if the model fails to emit the submit_sublabel
    tool call.
    """
    api = client or get_anthropic_client()
    system_prompt = _load_system_prompt()
    tool = _build_tool_schema()

    # No cache_control: Job Apps mail is sparse enough that even the 1h
    # TTL would mostly miss, and the cache-write penalty (1.25x input on
    # 5min, 2x on 1h) dominates. See docs/design.md.
    response = api.messages.create(
        model=model,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=system_prompt,
        tools=[tool],
        tool_choice={"type": "auto"},
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _USER_PREAMBLE},
                    {
                        "type": "text",
                        "text": _format_user_payload(
                            sender=sender, subject=subject, body_text=body_text
                        ),
                    },
                ],
            }
        ],
        extra_body={"output_config": {"effort": DEFAULT_EFFORT}},
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_sublabel":
            payload: Any = block.input
            if isinstance(payload, str):
                payload = parse_stringified_tool_input(
                    payload, context="sublabel classifier", stop_reason=response.stop_reason
                )
            classification = JobAppsClassification.model_validate(cast("dict[str, Any]", payload))
            return SublabelClassificationResult(
                classification=classification,
                usage=response.usage,
                model=model,
            )

    raise RuntimeError(
        "sublabel classifier response missing submit_sublabel tool_use block; "
        f"stop_reason={response.stop_reason!r}"
    )
