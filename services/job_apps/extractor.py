"""Extract email-derived fields the Notion writer needs.

Runs between the match cascade and the writer; every field is optional,
so a missed extraction degrades to a no-op rather than a bad write.
Design notes: docs/design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from langsmith import traceable
from pydantic import BaseModel, Field

from shared.anthropic_client import get_anthropic_client
from shared.llm_utils import parse_stringified_tool_input, truncate_body

if TYPE_CHECKING:
    from anthropic import Anthropic
    from anthropic.types import ToolParam, Usage

    from services.job_apps.types import ParsedEmail, Sublabel

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_EFFORT = "high"
DEFAULT_MAX_TOKENS = 16000

_PROMPT_PATH = Path(__file__).parent / "prompts" / "email_data_extractor.md"


class EmailMetadata(BaseModel):
    """Fields the Notion writer reads from email_metadata.

    Every field is optional; the writer's guards skip missing data
    rather than corrupting Notion.
    """

    interview_round: str | None = Field(
        default=None,
        description="recruiter_screen | phone_screen | onsite (Interview Scheduling only).",
    )
    assessment_deadline_date: date | None = None
    assessment_deadline_str: str | None = None
    assessment_platform: str | None = None
    proposed_screen_date: date | None = None
    recruiter_name: str | None = None
    recruiter_email: str | None = None
    offer_summary: str | None = None
    rejection_excerpt: str | None = None

    def to_writer_dict(self) -> dict[str, Any]:
        """Translate to the dict shape the writer expects."""
        return {
            "interview_round": self.interview_round,
            "assessment_deadline_date": self.assessment_deadline_date,
            "assessment_deadline_str": self.assessment_deadline_str,
            "assessment_platform": self.assessment_platform,
            "proposed_screen_date": self.proposed_screen_date,
            "recruiter_name": self.recruiter_name,
            "recruiter_email": self.recruiter_email,
            "offer_summary": self.offer_summary,
            "rejection_excerpt": self.rejection_excerpt,
        }


# Sublabels the writer reads metadata for. Status Update and Application
# Confirmation only advance status, so the graph node short-circuits
# them without an LLM call.
_EXTRACTING_SUBLABELS: frozenset[Sublabel] = frozenset(
    {"Interview Scheduling", "Assessment", "Recruiter Outreach", "Offer", "Rejection"}
)


@lru_cache(maxsize=1)
def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_tool_schema() -> ToolParam:
    return {
        "name": "submit_extraction",
        "description": (
            "Submit the structured fields extracted from the email. Call "
            "exactly once. Every field is optional — return null for "
            "anything the email does not contain."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "interview_round": {
                    "type": ["string", "null"],
                    "enum": ["recruiter_screen", "phone_screen", "onsite", None],
                    "description": "Interview round when sublabel is Interview Scheduling.",
                },
                "assessment_deadline_date": {
                    "type": ["string", "null"],
                    "description": "OA deadline as YYYY-MM-DD; null if not stated.",
                },
                "assessment_deadline_str": {
                    "type": ["string", "null"],
                    "description": "Human-readable deadline phrase if no parseable date.",
                },
                "assessment_platform": {
                    "type": ["string", "null"],
                    "description": "Platform name (HackerRank, CodeSignal, etc.).",
                },
                "proposed_screen_date": {
                    "type": ["string", "null"],
                    "description": "Recruiter-proposed screen date as YYYY-MM-DD.",
                },
                "recruiter_name": {"type": ["string", "null"]},
                "recruiter_email": {"type": ["string", "null"]},
                "offer_summary": {
                    "type": ["string", "null"],
                    "description": "1-2 sentence offer summary; plain prose.",
                },
                "rejection_excerpt": {
                    "type": ["string", "null"],
                    "description": "Up to 100-char verbatim excerpt of the rejection sentence.",
                },
            },
            "required": [
                "interview_round",
                "assessment_deadline_date",
                "assessment_deadline_str",
                "assessment_platform",
                "proposed_screen_date",
                "recruiter_name",
                "recruiter_email",
                "offer_summary",
                "rejection_excerpt",
            ],
        },
    }


def _format_user_payload(*, parsed_email: ParsedEmail, sublabel: Sublabel) -> str:
    return (
        f"sublabel: {sublabel}\n"
        f"sender: {parsed_email.sender}\n"
        f"subject: {parsed_email.subject}\n\n"
        f"body_text:\n{truncate_body(parsed_email.body_text)}\n"
    )


def _coerce_date(raw: Any) -> date | None:
    """Parse a YYYY-MM-DD string into a date, returning None on bad input.

    A drifted format from the model must not crash the runner; the worst
    case is the writer skipping that date field.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _coerce_str(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _validate_round(raw: Any) -> str | None:
    if raw in {"recruiter_screen", "phone_screen", "onsite"}:
        return cast("str", raw)
    return None


@dataclass(frozen=True)
class ExtractionResult:
    """An EmailMetadata plus the Anthropic call's usage and model.

    usage and model are None on the short-circuit path where no LLM ran.
    """

    metadata: EmailMetadata
    usage: Usage | None
    model: str | None


@traceable(run_type="llm", name="job_apps.extract")
def extract_email_metadata(
    *,
    parsed_email: ParsedEmail,
    sublabel: Sublabel,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> ExtractionResult:
    """Read the email and produce an EmailMetadata for the Notion writer.

    Sublabels that need no extraction (Status Update, Application
    Confirmation) short-circuit to an empty EmailMetadata without an LLM
    call, leaving usage and model None. Raises RuntimeError if the model
    fails to emit submit_extraction.
    """
    if sublabel not in _EXTRACTING_SUBLABELS:
        return ExtractionResult(metadata=EmailMetadata(), usage=None, model=None)

    api = client or get_anthropic_client()
    system_prompt = _load_prompt()
    tool = _build_tool_schema()

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
                "content": _format_user_payload(parsed_email=parsed_email, sublabel=sublabel),
            }
        ],
        extra_body={"output_config": {"effort": DEFAULT_EFFORT}},
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_extraction":
            payload: Any = block.input
            if isinstance(payload, str):
                payload = parse_stringified_tool_input(
                    payload, context="extractor", stop_reason=response.stop_reason
                )
            metadata = _validate_payload(cast("dict[str, Any]", payload))
            return ExtractionResult(metadata=metadata, usage=response.usage, model=model)

    raise RuntimeError(
        "extractor response missing submit_extraction tool_use block; "
        f"stop_reason={response.stop_reason!r}"
    )


def _validate_payload(payload: dict[str, Any]) -> EmailMetadata:
    """Coerce the model's tool-use input into EmailMetadata defensively."""
    return EmailMetadata(
        interview_round=_validate_round(payload.get("interview_round")),
        assessment_deadline_date=_coerce_date(payload.get("assessment_deadline_date")),
        assessment_deadline_str=_coerce_str(payload.get("assessment_deadline_str")),
        assessment_platform=_coerce_str(payload.get("assessment_platform")),
        proposed_screen_date=_coerce_date(payload.get("proposed_screen_date")),
        recruiter_name=_coerce_str(payload.get("recruiter_name")),
        recruiter_email=_coerce_str(payload.get("recruiter_email")),
        offer_summary=_coerce_str(payload.get("offer_summary")),
        rejection_excerpt=_coerce_str(payload.get("rejection_excerpt")),
    )


__all__ = [
    "DEFAULT_EFFORT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "EmailMetadata",
    "ExtractionResult",
    "extract_email_metadata",
]
