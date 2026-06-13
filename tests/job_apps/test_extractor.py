"""Tests for the email-data extractor.

Anthropic is mocked; prompt content is exercised by the env-gated live
test, not asserted here.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.job_apps.extractor import (
    EmailMetadata,
    ExtractionResult,
    _build_tool_schema,
    _coerce_date,
    _coerce_str,
    _validate_round,
    extract_email_metadata,
)
from services.job_apps.types import ParsedEmail, Sublabel

# ---------------------------------------------------------------------- helpers


def _email(
    *,
    sender: str = "Recruiter <recruiter@acme.com>",
    subject: str = "Phone screen",
    body_text: str = "Pick a slot via Calendly: https://calendly.com/x",
) -> ParsedEmail:
    return ParsedEmail(
        sender=sender,
        subject=subject,
        body_text=body_text,
        urls=[],
        received_at_utc=datetime(2026, 5, 5, 14, 0, tzinfo=UTC),
    )


def _llm_response(payload: dict[str, Any]) -> MagicMock:
    """Build a fake Anthropic response carrying a submit_extraction tool_use block."""
    response = MagicMock()
    response.stop_reason = "tool_use"
    block = MagicMock()
    block.type = "tool_use"
    block.name = "submit_extraction"
    block.input = payload
    response.content = [block]
    usage = MagicMock()
    usage.input_tokens = 50
    usage.output_tokens = 30
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0
    usage.cache_creation = None
    response.usage = usage
    return response


def _make_client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _full_null() -> dict[str, Any]:
    """Every required field set to null. The writer accepts this shape."""
    return {
        "interview_round": None,
        "assessment_deadline_date": None,
        "assessment_deadline_str": None,
        "assessment_platform": None,
        "proposed_screen_date": None,
        "recruiter_name": None,
        "recruiter_email": None,
        "offer_summary": None,
        "rejection_excerpt": None,
    }


# ------------------------------------------------------- short-circuit (no LLM)


@pytest.mark.parametrize("sublabel", ["Status Update", "Application Confirmation"])
def test_extract_skips_llm_for_sublabels_that_dont_need_metadata(
    sublabel: Sublabel,
) -> None:
    client = MagicMock()
    result = extract_email_metadata(parsed_email=_email(), sublabel=sublabel, client=client)
    assert isinstance(result, ExtractionResult)
    metadata = result.metadata
    assert isinstance(metadata, EmailMetadata)
    # Every field is null on the short-circuit path.
    assert metadata.model_dump() == EmailMetadata().model_dump()
    # No LLM ran, so no billing data.
    assert result.usage is None
    assert result.model is None
    client.messages.create.assert_not_called()


# ---------------------------------------------------------- sublabel-specific


def test_extract_interview_scheduling_phone_screen() -> None:
    payload = _full_null()
    payload["interview_round"] = "phone_screen"
    client = _make_client(_llm_response(payload))
    result = extract_email_metadata(
        parsed_email=_email(),
        sublabel="Interview Scheduling",
        client=client,
    )
    assert result.metadata.interview_round == "phone_screen"
    # Billing data captured for non-short-circuit path.
    assert result.usage is not None
    assert result.model == "claude-opus-4-7"
    client.messages.create.assert_called_once()
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-opus-4-7"
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["extra_body"] == {"output_config": {"effort": "high"}}


def test_extract_interview_scheduling_invalid_round_coerces_to_none() -> None:
    """A hallucinated round value becomes null rather than corrupting the writer."""
    payload = _full_null()
    payload["interview_round"] = "lunch_with_ceo"
    client = _make_client(_llm_response(payload))
    result = extract_email_metadata(
        parsed_email=_email(),
        sublabel="Interview Scheduling",
        client=client,
    )
    assert result.metadata.interview_round is None


def test_extract_assessment_with_deadline_and_platform() -> None:
    payload = _full_null()
    payload["assessment_deadline_date"] = "2026-05-12"
    payload["assessment_platform"] = "HackerRank"
    payload["assessment_deadline_str"] = "by EOD Tuesday"
    client = _make_client(_llm_response(payload))
    result = extract_email_metadata(
        parsed_email=_email(subject="Your HackerRank assessment"),
        sublabel="Assessment",
        client=client,
    )
    assert result.metadata.assessment_deadline_date == date(2026, 5, 12)
    assert result.metadata.assessment_platform == "HackerRank"
    assert result.metadata.assessment_deadline_str == "by EOD Tuesday"


def test_extract_recruiter_outreach_with_proposed_date_and_contact() -> None:
    payload = _full_null()
    payload["proposed_screen_date"] = "2026-05-08"
    payload["recruiter_name"] = "Jane Park"
    payload["recruiter_email"] = "jane@acme.com"
    client = _make_client(_llm_response(payload))
    result = extract_email_metadata(
        parsed_email=_email(),
        sublabel="Recruiter Outreach",
        client=client,
    )
    assert result.metadata.proposed_screen_date == date(2026, 5, 8)
    assert result.metadata.recruiter_name == "Jane Park"
    assert result.metadata.recruiter_email == "jane@acme.com"


def test_extract_offer_with_summary() -> None:
    payload = _full_null()
    payload["offer_summary"] = "Base $150k, 0.1% equity, sign-on $20k. Decision by 5/15."
    client = _make_client(_llm_response(payload))
    result = extract_email_metadata(
        parsed_email=_email(),
        sublabel="Offer",
        client=client,
    )
    assert result.metadata.offer_summary is not None
    assert "150k" in result.metadata.offer_summary


def test_extract_rejection_with_excerpt() -> None:
    payload = _full_null()
    payload["rejection_excerpt"] = "we've decided to move forward with other candidates"
    client = _make_client(_llm_response(payload))
    result = extract_email_metadata(
        parsed_email=_email(),
        sublabel="Rejection",
        client=client,
    )
    assert (
        result.metadata.rejection_excerpt == "we've decided to move forward with other candidates"
    )


# ---------------------------------------------------------- defensive coercion


def test_extract_invalid_date_coerces_to_none() -> None:
    """A malformed date string from the model becomes null rather than crashing."""
    payload = _full_null()
    payload["assessment_deadline_date"] = "April 30, 2026"
    client = _make_client(_llm_response(payload))
    result = extract_email_metadata(
        parsed_email=_email(),
        sublabel="Assessment",
        client=client,
    )
    assert result.metadata.assessment_deadline_date is None


def test_extract_response_missing_tool_use_raises() -> None:
    response = MagicMock()
    response.stop_reason = "end_turn"
    text_block = MagicMock()
    text_block.type = "text"
    response.content = [text_block]
    client = _make_client(response)
    with pytest.raises(RuntimeError, match="missing submit_extraction"):
        extract_email_metadata(
            parsed_email=_email(),
            sublabel="Interview Scheduling",
            client=client,
        )


def test_extract_handles_stringified_tool_input() -> None:
    """Stringified tool input is re-parsed via JSON."""
    payload = _full_null()
    payload["interview_round"] = "onsite"
    response = MagicMock()
    response.stop_reason = "tool_use"
    block = MagicMock()
    block.type = "tool_use"
    block.name = "submit_extraction"
    block.input = json.dumps(payload)
    response.content = [block]
    usage = MagicMock()
    usage.input_tokens = 10
    usage.output_tokens = 5
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0
    usage.cache_creation = None
    response.usage = usage
    client = _make_client(response)
    result = extract_email_metadata(
        parsed_email=_email(),
        sublabel="Interview Scheduling",
        client=client,
    )
    assert result.metadata.interview_round == "onsite"


def test_extract_recovers_invalid_escape_in_stringified_offer_summary() -> None:
    """A stringified payload with an invalid \\$ escape still parses via the shared recovery."""
    raw = (
        '{"interview_round": null, "assessment_deadline_date": null, '
        '"assessment_deadline_str": null, "assessment_platform": null, '
        '"proposed_screen_date": null, "recruiter_name": null, '
        '"recruiter_email": null, '
        '"offer_summary": "Base \\$150k plus equity.", "rejection_excerpt": null}'
    )
    response = _llm_response({})
    response.content[0].input = raw
    client = _make_client(response)
    result = extract_email_metadata(parsed_email=_email(), sublabel="Offer", client=client)
    assert result.metadata.offer_summary == "Base $150k plus equity."


# ---------------------------------------------------------- helper-level units


def test_to_writer_dict_emits_every_writer_key() -> None:
    metadata = EmailMetadata(
        interview_round="onsite",
        assessment_deadline_date=date(2026, 5, 1),
        assessment_platform="HackerRank",
    )
    out = metadata.to_writer_dict()
    expected_keys = {
        "interview_round",
        "assessment_deadline_date",
        "assessment_deadline_str",
        "assessment_platform",
        "proposed_screen_date",
        "recruiter_name",
        "recruiter_email",
        "offer_summary",
        "rejection_excerpt",
    }
    assert set(out.keys()) == expected_keys
    assert out["interview_round"] == "onsite"
    assert out["assessment_deadline_date"] == date(2026, 5, 1)
    assert out["assessment_platform"] == "HackerRank"
    assert out["recruiter_name"] is None


def test_coerce_date_parses_iso_or_returns_none() -> None:
    assert _coerce_date("2026-05-12") == date(2026, 5, 12)
    assert _coerce_date(None) is None
    assert _coerce_date("") is None
    assert _coerce_date("not a date") is None
    assert _coerce_date("2026-13-99") is None
    # Already a date object.
    assert _coerce_date(date(2026, 5, 12)) == date(2026, 5, 12)


def test_coerce_str_strips_whitespace_and_treats_empty_as_none() -> None:
    assert _coerce_str("  HackerRank  ") == "HackerRank"
    assert _coerce_str("") is None
    assert _coerce_str("   ") is None
    assert _coerce_str(None) is None


def test_validate_round_passes_valid_only() -> None:
    assert _validate_round("recruiter_screen") == "recruiter_screen"
    assert _validate_round("phone_screen") == "phone_screen"
    assert _validate_round("onsite") == "onsite"
    assert _validate_round("video") is None
    assert _validate_round(None) is None


def test_extract_tool_schema_shape() -> None:
    schema = _build_tool_schema()
    assert schema["name"] == "submit_extraction"
    input_schema = schema["input_schema"]
    assert isinstance(input_schema, dict)
    required = input_schema["required"]
    assert isinstance(required, list)
    assert "interview_round" in required
    assert "assessment_deadline_date" in required
    assert "rejection_excerpt" in required
