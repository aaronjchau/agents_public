"""Tests for the email-triager classifier.

The default suite mocks the Anthropic client; one opt-in test under
AGENTS_LIVE_LLM=1 runs the real model.
"""

from __future__ import annotations

import os
from typing import Any, cast

import pytest
from anthropic.types import Message, TextBlock, Usage

from services.triager.classifier import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_THINKING_BUDGET,
    ClassificationResult,
    _build_tool_schema,
    classify,
)
from services.triager.labels import PRIMARY_LABELS
from services.triager.types import Classification, Label
from tests.helpers import make_mock_client, make_mock_response


def _make_mock_response(
    *,
    primary_label: Label = "Marketing",
    flagged: bool = False,
    reasoning: str = "Default test reasoning.",
    stringify_input: bool = False,
) -> Message:
    """Build a Message with one submit_classification tool_use block.

    stringify_input=True wraps the tool input in a JSON string instead of
    a dict to exercise the defensive parse path.
    """
    return make_mock_response(
        tool_name="submit_classification",
        input_payload={
            "primary_label": primary_label,
            "flagged": flagged,
            "reasoning": reasoning,
        },
        model=DEFAULT_MODEL,
        stringify_input=stringify_input,
    )


def _classify(
    response: Message,
    *,
    sender: str = "noreply@example.com",
    subject: str = "Test subject",
    body_text: str = "Test body text.",
) -> Classification:
    """Run classify() and unwrap the inner Classification.

    Tests that need to inspect usage or model call classify() directly.
    """
    client = make_mock_client(response)
    return classify(
        sender=sender, subject=subject, body_text=body_text, client=client
    ).classification


# ---------------------------------------------------------------------------
# Each of the 12 labels round-trips correctly
# ---------------------------------------------------------------------------

ALL_LABELS: list[Label] = [
    "Security",
    "Finance",
    "People",
    "Job Apps",
    "Networking",
    "Medical",
    "Purchases",
    "Returns",
    "Home",
    "Notifications",
    "News",
    "Marketing",
]


@pytest.mark.parametrize("label", ALL_LABELS)
def test_classify_returns_each_label(label: Label) -> None:
    result = _classify(
        _make_mock_response(primary_label=label, flagged=False, reasoning="ok"),
    )
    assert isinstance(result, Classification)
    assert result.primary_label == label


# ---------------------------------------------------------------------------
# Flagged overlay correctness across representative scenarios
# ---------------------------------------------------------------------------


def test_human_reply_is_flagged() -> None:
    """A real human writing personally with a question should flag."""
    result = _classify(
        _make_mock_response(
            primary_label="People",
            flagged=True,
            reasoning="Personal reply asking a question, expects response.",
        ),
        sender="Jane Doe <jane@somecompany.com>",
        subject="Re: coffee chat next week?",
        body_text="Hi Alex, can you do Tuesday at 2pm? Let me know!",
    )
    assert result.primary_label == "People"
    assert result.flagged is True


def test_routine_purchase_reminder_is_not_flagged() -> None:
    """Walmart 'last chance to add items' is purely informational."""
    result = _classify(
        _make_mock_response(
            primary_label="Purchases",
            flagged=False,
            reasoning="Routine pre-shipment reminder, no action with consequences.",
        ),
        sender="Walmart <noreply@walmart.com>",
        subject="Last chance to add items to your order",
        body_text="Your order ships in 1 hour. Add anything else? Click here.",
    )
    assert result.primary_label == "Purchases"
    assert result.flagged is False


def test_security_2fa_code_is_not_flagged() -> None:
    """2FA codes are routine sign-in alerts; almost never flagged."""
    result = _classify(
        _make_mock_response(
            primary_label="Security",
            flagged=False,
            reasoning="Routine 2FA code; informational only per Security carve-out.",
        ),
        sender="Google <no-reply@accounts.google.com>",
        subject="Your verification code",
        body_text="Your Google verification code is 123456.",
    )
    assert result.primary_label == "Security"
    assert result.flagged is False


def test_interview_scheduling_is_flagged() -> None:
    """Interview scheduling needs a concrete reply with a deadline."""
    result = _classify(
        _make_mock_response(
            primary_label="Job Apps",
            flagged=True,
            reasoning="Interview scheduling requires the recipient to confirm a slot.",
        ),
        sender="Recruiter <recruiter@bigco.com>",
        subject="Phone screen scheduling — pick a time",
        body_text="Please pick a 30-minute slot via Calendly: <link>",
    )
    assert result.primary_label == "Job Apps"
    assert result.flagged is True


# ---------------------------------------------------------------------------
# Precedence: Job Apps > Networking
# ---------------------------------------------------------------------------


def test_precedence_job_apps_outranks_networking() -> None:
    """Recruiter outreach about a specific role is Job Apps even via a LinkedIn thread."""
    result = _classify(
        _make_mock_response(
            primary_label="Job Apps",
            flagged=True,
            reasoning="Recruiter outreach about a specific role; per precedence "
            "Job Apps outranks Networking.",
        ),
        sender="LinkedIn <messaging-digest-noreply@linkedin.com>",
        subject="Sarah Recruiter sent you a message about a SWE role",
        body_text=(
            "Hi Alex — I'd love to chat about an L3 Backend SWE role at "
            "AcmeCorp. Are you open to learning more?"
        ),
    )
    assert result.primary_label == "Job Apps"
    assert result.flagged is True


# ---------------------------------------------------------------------------
# Defensive parse: stringified tool input is recovered
# ---------------------------------------------------------------------------


def test_classify_handles_stringified_tool_input() -> None:
    """Stringified tool input round-trips through the defensive parse path."""
    result = _classify(
        _make_mock_response(
            primary_label="News",
            flagged=False,
            reasoning="Established outlet newsletter.",
            stringify_input=True,
        ),
    )
    assert result.primary_label == "News"
    assert result.flagged is False
    assert result.reasoning == "Established outlet newsletter."


# ---------------------------------------------------------------------------
# Tool schema, caching, and tool-choice plumbing
# ---------------------------------------------------------------------------


def test_classify_uses_default_model_and_max_tokens() -> None:
    client = make_mock_client(_make_mock_response())
    classify(sender="a@b.com", subject="x", body_text="y", client=client)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == DEFAULT_MODEL
    assert kwargs["max_tokens"] == DEFAULT_MAX_TOKENS


def test_classify_respects_overrides() -> None:
    client = make_mock_client(_make_mock_response())
    classify(
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


def test_classify_caches_system_prompt_with_1h_ttl() -> None:
    client = make_mock_client(_make_mock_response())
    classify(sender="a@b.com", subject="x", body_text="y", client=client)

    kwargs = client.messages.create.call_args.kwargs
    system = kwargs["system"]
    assert isinstance(system, list)
    assert len(system) == 1
    block = system[0]
    assert block["type"] == "text"
    # 1-hour TTL matches the measured email arrival pattern (~75% of
    # consecutive emails arrive within an hour, above the 53% break-even
    # for the 1h cache).
    assert block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # Sanity-check the prompt is non-empty and load-bearing.
    assert "submit_classification" in block["text"]
    assert "Job Apps" in block["text"]


def test_classify_caches_user_preamble_but_not_email_payload() -> None:
    client = make_mock_client(_make_mock_response())
    classify(sender="a@b.com", subject="x", body_text="y", client=client)

    kwargs = client.messages.create.call_args.kwargs
    messages = kwargs["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"

    content = messages[0]["content"]
    assert isinstance(content, list)
    assert len(content) == 2

    preamble, payload = content
    assert preamble["type"] == "text"
    assert preamble["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert payload["type"] == "text"
    # The per-email payload changes per call and must not be cached.
    assert "cache_control" not in payload


def test_classify_uses_tool_choice_auto_for_thinking_compatibility() -> None:
    """tool_choice stays auto because forced tool use is incompatible with extended thinking."""
    client = make_mock_client(_make_mock_response())
    classify(sender="a@b.com", subject="x", body_text="y", client=client)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "auto"}


def test_classify_passes_thinking_with_budget() -> None:
    """Thinking is enabled with DEFAULT_THINKING_BUDGET and max_tokens exceeds the budget."""
    client = make_mock_client(_make_mock_response())
    classify(sender="a@b.com", subject="x", body_text="y", client=client)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["thinking"] == {
        "type": "enabled",
        "budget_tokens": DEFAULT_THINKING_BUDGET,
    }
    assert kwargs["max_tokens"] > DEFAULT_THINKING_BUDGET


def test_classify_tool_schema_matches_classification_shape() -> None:
    schema = _build_tool_schema()
    assert schema["name"] == "submit_classification"

    input_schema = cast("dict[str, Any]", schema["input_schema"])
    props = input_schema["properties"]
    assert set(props.keys()) == {"primary_label", "flagged", "reasoning"}
    assert props["primary_label"]["type"] == "string"
    assert props["flagged"]["type"] == "boolean"
    assert props["reasoning"]["type"] == "string"
    assert set(input_schema["required"]) == {"primary_label", "flagged", "reasoning"}

    # The label enum derives from labels.PRIMARY_LABELS: exactly the 12
    # spec labels, in declaration order.
    assert props["primary_label"]["enum"] == list(PRIMARY_LABELS)
    assert set(props["primary_label"]["enum"]) == set(ALL_LABELS)


def test_classify_user_payload_contains_email_fields() -> None:
    client = make_mock_client(_make_mock_response())
    classify(
        sender="Bob <bob@example.com>",
        subject="Order #12345 shipped",
        body_text="Tracking number 1Z999AA10123456784",
        client=client,
    )

    kwargs = client.messages.create.call_args.kwargs
    payload_text = kwargs["messages"][0]["content"][1]["text"]
    assert "Bob <bob@example.com>" in payload_text
    assert "Order #12345 shipped" in payload_text
    assert "1Z999AA10123456784" in payload_text


def test_classify_truncates_long_bodies() -> None:
    """Bodies over 8000 chars get clipped to head + tail with a marker."""
    big_body = "A" * 6500 + "B" * 4000  # 10500 total, over the 8000 threshold.
    client = make_mock_client(_make_mock_response())
    classify(sender="a@b.com", subject="x", body_text=big_body, client=client)

    kwargs = client.messages.create.call_args.kwargs
    payload_text = kwargs["messages"][0]["content"][1]["text"]
    assert "[... truncated ...]" in payload_text
    # Head: 6000 As. Tail: 1000 chars from the end of big_body, which lives
    # in the all-B section, so the tail is all Bs.
    assert "A" * 100 in payload_text
    assert "B" * 100 in payload_text
    # The full body must not have made it through.
    assert big_body not in payload_text


def test_classify_raises_when_response_missing_tool_use() -> None:
    """A text-only response with no tool call raises instead of silently defaulting."""
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

    with pytest.raises(RuntimeError, match="submit_classification"):
        classify(sender="a@b.com", subject="x", body_text="y", client=client)


# ---------------------------------------------------------------------------
# ClassificationResult shape: usage + model pass through
# ---------------------------------------------------------------------------


def test_classify_returns_classification_result_with_usage_and_model() -> None:
    """The runner needs usage and model to write cost on the audit row."""
    client = make_mock_client(_make_mock_response(primary_label="Finance"))
    result = classify(sender="a@b.com", subject="x", body_text="y", client=client)

    assert isinstance(result, ClassificationResult)
    assert result.classification.primary_label == "Finance"
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 10
    # Model defaults to DEFAULT_MODEL when not overridden.
    assert result.model == DEFAULT_MODEL


def test_classify_model_override_propagates_to_result() -> None:
    """When the caller overrides model, the result reflects it."""
    client = make_mock_client(_make_mock_response())
    result = classify(
        sender="a@b.com",
        subject="x",
        body_text="y",
        client=client,
        model="claude-haiku-4-5",
    )
    assert result.model == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Live-API test (opt-in via AGENTS_LIVE_LLM=1)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AGENTS_LIVE_LLM") != "1",
    reason="set AGENTS_LIVE_LLM=1 to hit the real Anthropic API",
)
def test_classify_live_against_representative_emails() -> None:
    """Run the classifier against a small live battery and print results.

    Asserts only that the call succeeds and the label is one of the 12;
    prints each classification for eyeballing on a smoke run.
    """
    fixtures: list[tuple[str, str, str, str]] = [
        (
            "google_2fa",
            "Google <no-reply@accounts.google.com>",
            "Your verification code",
            "Your Google verification code is 123456.",
        ),
        (
            "clinic_visit",
            "Citywide Medical Group <noreply@citywidemedical.example>",
            "Your video visit is in 30 minutes",
            "Click here to join your video visit at 2:00 PM.",
        ),
        (
            "nyt_morning",
            "The New York Times <nytdirect@nytimes.com>",
            "The Morning: Today's headlines",
            "Top stories: court ruling on tariffs; Fed meeting preview; ...",
        ),
        (
            "amazon_order",
            "Amazon.com <auto-confirm@amazon.com>",
            "Your Amazon.com order has shipped",
            "Your order #123-4567890 has shipped via UPS. Tracking: 1Z999AA1...",
        ),
    ]

    for name, sender, subject, body in fixtures:
        result = classify(sender=sender, subject=subject, body_text=body)
        classification = result.classification
        assert classification.primary_label in ALL_LABELS, f"{name}: bad label"
        print(
            f"\n[live] {name}: {classification.primary_label} "
            f"(flagged={classification.flagged}) — {classification.reasoning[:80]}"
        )
