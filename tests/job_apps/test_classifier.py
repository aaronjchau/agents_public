"""Tests for the Job Apps sublabel classifier.

Anthropic is mocked; an opt-in live battery runs under AGENTS_LIVE_LLM=1.
"""

from __future__ import annotations

import os
from typing import Any, cast

import pytest
from anthropic.types import Message, TextBlock, Usage

from services.job_apps.classifier import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    SublabelClassificationResult,
    _build_tool_schema,
    classify_sublabel,
)
from services.job_apps.types import JobAppsClassification, Sublabel
from tests.helpers import make_mock_client, make_mock_response


def _make_mock_response(
    *,
    sublabel: Sublabel = "Status Update",
    reasoning: str = "Default test reasoning.",
    stringify_input: bool = False,
) -> Message:
    """A Message with one submit_sublabel tool_use block.

    stringify_input=True wraps the tool input in a JSON string to
    exercise the defensive parse path.
    """
    return make_mock_response(
        tool_name="submit_sublabel",
        input_payload={"sublabel": sublabel, "reasoning": reasoning},
        model=DEFAULT_MODEL,
        stringify_input=stringify_input,
    )


def _classify(
    response: Message,
    *,
    sender: str = "noreply@example.com",
    subject: str = "Test subject",
    body_text: str = "Test body text.",
) -> JobAppsClassification:
    """Drive classify_sublabel and return the unwrapped JobAppsClassification."""
    client = make_mock_client(response)
    result = classify_sublabel(
        sender=sender,
        subject=subject,
        body_text=body_text,
        client=client,
    )
    return result.classification


# ---------------------------------------------------------------------------
# Each of the 7 sublabels round-trips correctly
# ---------------------------------------------------------------------------

ALL_SUBLABELS: list[Sublabel] = [
    "Offer",
    "Interview Scheduling",
    "Assessment",
    "Recruiter Outreach",
    "Status Update",
    "Application Confirmation",
    "Rejection",
]


@pytest.mark.parametrize("sublabel", ALL_SUBLABELS)
def test_classify_returns_each_sublabel(sublabel: Sublabel) -> None:
    result = _classify(_make_mock_response(sublabel=sublabel, reasoning="ok"))
    assert isinstance(result, JobAppsClassification)
    assert result.sublabel == sublabel
    assert result.reasoning == "ok"


# ---------------------------------------------------------------------------
# Defensive parse: stringified tool input is recovered
# ---------------------------------------------------------------------------


def test_classify_handles_stringified_tool_input() -> None:
    """A JSON-stringified tool input round-trips through the defensive parse."""
    result = _classify(
        _make_mock_response(
            sublabel="Rejection",
            reasoning="Standard rejection language.",
            stringify_input=True,
        ),
    )
    assert result.sublabel == "Rejection"
    assert result.reasoning == "Standard rejection language."


# ---------------------------------------------------------------------------
# Tool schema, plumbing, and request shape
# ---------------------------------------------------------------------------


def test_classify_uses_default_model_and_max_tokens() -> None:
    client = make_mock_client(_make_mock_response())
    classify_sublabel(sender="a@b.com", subject="x", body_text="y", client=client)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == DEFAULT_MODEL
    assert kwargs["max_tokens"] == DEFAULT_MAX_TOKENS


def test_classify_respects_overrides() -> None:
    client = make_mock_client(_make_mock_response())
    classify_sublabel(
        sender="a@b.com",
        subject="x",
        body_text="y",
        client=client,
        model="claude-haiku-4-5",
        max_tokens=200,
    )

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["max_tokens"] == 200


def test_classify_does_not_set_cache_control() -> None:
    """No cache_control on the system prompt or user content blocks."""
    client = make_mock_client(_make_mock_response())
    classify_sublabel(sender="a@b.com", subject="x", body_text="y", client=client)

    kwargs = client.messages.create.call_args.kwargs

    # A plain-string system prompt has no place to hang cache_control;
    # that alone is the contract.
    system = kwargs["system"]
    assert isinstance(system, str)
    assert "submit_sublabel" in system

    # User content blocks must not carry cache_control either.
    content = kwargs["messages"][0]["content"]
    for block in content:
        assert "cache_control" not in block, f"unexpected cache_control on user block: {block}"


def test_classify_uses_tool_choice_auto_for_thinking_compatibility() -> None:
    """tool_choice stays auto; forced tool use is incompatible with extended thinking."""
    client = make_mock_client(_make_mock_response())
    classify_sublabel(sender="a@b.com", subject="x", body_text="y", client=client)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "auto"}


def test_classify_passes_adaptive_thinking_and_high_effort() -> None:
    """Adaptive thinking with effort=high rides in extra_body.output_config."""
    client = make_mock_client(_make_mock_response())
    classify_sublabel(sender="a@b.com", subject="x", body_text="y", client=client)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["extra_body"] == {"output_config": {"effort": DEFAULT_EFFORT}}
    assert kwargs["max_tokens"] == DEFAULT_MAX_TOKENS


def test_classify_returns_billing_data() -> None:
    """The result carries usage and model alongside the classification."""
    client = make_mock_client(_make_mock_response(sublabel="Rejection", reasoning="ok"))
    result = classify_sublabel(sender="a@b.com", subject="x", body_text="y", client=client)
    assert isinstance(result, SublabelClassificationResult)
    assert result.classification.sublabel == "Rejection"
    assert result.model == DEFAULT_MODEL
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 10


def test_classify_tool_schema_matches_classification_shape() -> None:
    schema = _build_tool_schema()
    assert schema["name"] == "submit_sublabel"

    input_schema = cast("dict[str, Any]", schema["input_schema"])
    props = input_schema["properties"]
    assert set(props.keys()) == {"sublabel", "reasoning"}
    assert props["sublabel"]["type"] == "string"
    assert props["reasoning"]["type"] == "string"
    assert set(input_schema["required"]) == {"sublabel", "reasoning"}

    # Sublabel enum must contain exactly the 7 spec sublabels.
    assert set(props["sublabel"]["enum"]) == set(ALL_SUBLABELS)
    assert len(props["sublabel"]["enum"]) == 7


def test_classify_user_payload_contains_email_fields() -> None:
    client = make_mock_client(_make_mock_response())
    classify_sublabel(
        sender="Bob <bob@example.com>",
        subject="Phone screen scheduling",
        body_text="Please pick a slot via Calendly.",
        client=client,
    )

    kwargs = client.messages.create.call_args.kwargs
    payload_text = kwargs["messages"][0]["content"][1]["text"]
    assert "Bob <bob@example.com>" in payload_text
    assert "Phone screen scheduling" in payload_text
    assert "Calendly" in payload_text


def test_classify_truncates_long_bodies() -> None:
    """Bodies over 8000 chars get clipped to head + tail with a marker."""
    big_body = "A" * 6500 + "B" * 4000  # 10500 total, over the 8000 threshold.
    client = make_mock_client(_make_mock_response())
    classify_sublabel(sender="a@b.com", subject="x", body_text=big_body, client=client)

    kwargs = client.messages.create.call_args.kwargs
    payload_text = kwargs["messages"][0]["content"][1]["text"]
    assert "[... truncated ...]" in payload_text
    # Head: first 6000 chars (all A). Tail: last 1000 chars of big_body
    # (entirely in the B section).
    assert "A" * 100 in payload_text
    assert "B" * 100 in payload_text
    # The full body must not survive truncation.
    assert big_body not in payload_text


def test_classify_raises_when_response_missing_tool_use() -> None:
    """A text-only response raises loudly instead of silently defaulting."""
    text_only = Message(
        id="msg_test_text",
        type="message",
        role="assistant",
        model=DEFAULT_MODEL,
        content=[TextBlock(type="text", text="I refuse.", citations=None)],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(input_tokens=5, output_tokens=5),
    )
    client = make_mock_client(text_only)

    with pytest.raises(RuntimeError, match="submit_sublabel"):
        classify_sublabel(sender="a@b.com", subject="x", body_text="y", client=client)


# ---------------------------------------------------------------------------
# Live-API test (opt-in via AGENTS_LIVE_LLM=1)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AGENTS_LIVE_LLM") != "1",
    reason="set AGENTS_LIVE_LLM=1 to hit the real Anthropic API",
)
def test_classify_live_against_representative_emails() -> None:
    """Run the classifier against a small live battery and print results.

    Asserts only that each call succeeds with a valid sublabel; the
    printed classifications are for spot-checking quality.
    """
    fixtures: list[tuple[str, str, str, str]] = [
        (
            "offer",
            "Jane HR <jane@bigco.com>",
            "Your offer from BigCo",
            "We are pleased to extend an offer for the SWE role. Base $180k, "
            "equity, sign-on bonus. Please respond by Friday.",
        ),
        (
            "scheduling",
            "Recruiter <recruiter@bigco.com>",
            "Phone screen — pick a time",
            "Please pick a 30-minute slot via Calendly: https://calendly.com/x",
        ),
        (
            "assessment",
            "HackerRank <noreply@hackerrank.com>",
            "Coding assessment for BigCo",
            "Please complete the assessment within 48 hours.",
        ),
        (
            "rejection",
            "BigCo <noreply@bigco.com>",
            "Update on your application",
            "Unfortunately, we have decided to move forward with other candidates.",
        ),
        (
            "ats_confirm",
            "BigCo <no-reply@greenhouse.io>",
            "Thanks for applying to BigCo",
            "Thanks for applying — we received your application.",
        ),
        # Disambiguation scenarios; the rules live in the system prompt,
        # so they only mean anything against the real model. Expected per
        # spec: scheduling on an active app is Interview Scheduling, a
        # touch-base on an existing thread is Status Update, and screen
        # plus OA in one email prefers the more advanced state.
        (
            "disambig_active_app_scheduling",
            "Sarah Recruiter <sarah@bigco.com>",
            "Re: Software Engineer application — let's chat",
            "Hi Alex — I'd love to set up a phone screen for the SWE role you "
            "applied for. Are you free Tuesday or Thursday afternoon?",
        ),
        (
            "disambig_active_app_touch_base",
            "Sarah Recruiter <sarah@bigco.com>",
            "Re: Software Engineer application",
            "Hi Alex — taking over from my colleague on this thread. Just wanted "
            "to introduce myself; the team is still reviewing your application.",
        ),
        (
            "disambig_prefer_advanced_state",
            "Recruiter <recruiter@bigco.com>",
            "Next steps: phone screen + assessment",
            "Hi Alex — happy to set up a phone screen for next week (Calendly: "
            "https://calendly.com/example/screen). We'll also send you a short "
            "HackerRank afterwards.",
        ),
    ]

    for name, sender, subject, body in fixtures:
        result = classify_sublabel(sender=sender, subject=subject, body_text=body)
        classification = result.classification
        assert classification.sublabel in ALL_SUBLABELS, f"{name}: bad sublabel"
        print(f"\n[live] {name}: {classification.sublabel} — {classification.reasoning[:80]}")
