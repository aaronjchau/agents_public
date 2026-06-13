"""Tests for the Job Apps LangGraph orchestrator.

Anthropic, googleapiclient, and the ntn subprocess are all mocked; the
compiled graph runs end to end on canned node results. The pipeline
creates no drafts; the never-send guard lives in test_no_send.py.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.job_apps.classifier import SublabelClassificationResult
from services.job_apps.extractor import EmailMetadata as _EmailMetadata
from services.job_apps.extractor import ExtractionResult as _ExtractionResult
from services.job_apps.graph import (
    JobAppsState,
    compile_job_apps_graph,
    router_after_apply,
    router_after_classify,
    router_after_match,
)
from services.job_apps.notion_match import MatchCascadeResult
from services.job_apps.types import (
    JobAppsClassification,
    MatchResult,
    ParsedEmail,
    Sublabel,
    WriteResult,
)

# ----------------------------------------------------------------- fixtures


def _parsed_email(
    *,
    sender: str = "Recruiter <recruiter@bigco.com>",
    subject: str = "Phone screen for Backend SWE",
    body_text: str = "Pick a slot via Calendly: https://calendly.com/x",
) -> ParsedEmail:
    return ParsedEmail(
        sender=sender,
        subject=subject,
        body_text=body_text,
        urls=[],
        received_at_utc=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
    )


def _gmail_service_with_message(
    *,
    message_id: str = "m-1",
    thread_id: str = "t-1",
    sender: str = "Recruiter <recruiter@bigco.com>",
    subject: str = "Phone screen for Backend SWE",
    body_text: str = "Pick a time via Calendly.",
    header_message_id: str = "<msg-1@bigco.com>",
) -> MagicMock:
    """Build a Gmail service mock whose messages.get returns a full payload.

    Body data is base64url-encoded so extract_plain_text round-trips.
    """
    import base64

    body_b64 = base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ascii")
    message = {
        "id": message_id,
        "threadId": thread_id,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Tue, 05 May 2026 12:00:00 +0000"},
                {"name": "Message-ID", "value": header_message_id},
            ],
            "body": {"data": body_b64},
        },
    }
    service = MagicMock()
    service.users.return_value.messages.return_value.get.return_value.execute.return_value = message
    return service


def _fake_usage(input_tokens: int = 100, output_tokens: int = 20) -> Any:
    """Build a MagicMock that walks like anthropic.types.Usage."""
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0
    usage.cache_creation = None
    return usage


def _classification(sublabel: Sublabel, reasoning: str = "test") -> SublabelClassificationResult:
    """Build the wrapper the classifier returns, with placeholder billing data."""
    return SublabelClassificationResult(
        classification=JobAppsClassification(sublabel=sublabel, reasoning=reasoning),
        usage=_fake_usage(),
        model="claude-opus-4-7",
    )


def _match_cascade(
    status: str,
    notion_row_id: str | None = None,
    *,
    candidates: list[dict[str, Any]] | None = None,
    with_llm: bool = True,
) -> MatchCascadeResult:
    """Wrap a MatchResult in MatchCascadeResult; with_llm=False simulates the URL short-circuit."""
    cascade_kwargs: dict[str, Any] = {
        "result": MatchResult(
            status=status,  # type: ignore[arg-type]
            notion_row_id=notion_row_id,
            candidates=candidates or [],
        )
    }
    if with_llm:
        cascade_kwargs["usage"] = _fake_usage(input_tokens=300, output_tokens=50)
        cascade_kwargs["model"] = "claude-opus-4-7"
    else:
        cascade_kwargs["usage"] = None
        cascade_kwargs["model"] = None
    return MatchCascadeResult(**cascade_kwargs)


def _extraction(metadata: _EmailMetadata | None = None, with_llm: bool = True) -> _ExtractionResult:
    """Wrap EmailMetadata in ExtractionResult; with_llm=False for the short-circuit path."""
    return _ExtractionResult(
        metadata=metadata or _EmailMetadata(),
        usage=_fake_usage(input_tokens=400, output_tokens=80) if with_llm else None,
        model="claude-opus-4-7" if with_llm else None,
    )


def _build_test_graph(*, service: MagicMock) -> Any:
    """Assemble a fresh compiled graph with a fake service factory."""
    return compile_job_apps_graph(service_factory=lambda: service)


# --------------------------------------------------------------- router tests


class TestRouterAfterClassify:
    """Branching after the sublabel classifier."""

    @pytest.mark.parametrize(
        "sublabel",
        [
            "Rejection",
            "Offer",
            "Status Update",
            "Application Confirmation",
            "Interview Scheduling",
            "Assessment",
            "Recruiter Outreach",
        ],
    )
    def test_every_sublabel_routes_to_apply(self, sublabel: Sublabel) -> None:
        state = JobAppsState(message_id="m", sublabel=sublabel)
        assert router_after_classify(state) == "apply"

    def test_node_error_routes_to_error_branch(self) -> None:
        state = JobAppsState(message_id="m", terminal_reason="node_error")
        assert router_after_classify(state) == "error"

    def test_missing_sublabel_routes_to_error_branch(self) -> None:
        state = JobAppsState(message_id="m")
        assert router_after_classify(state) == "error"


class TestRouterAfterApply:
    """Per-sublabel branching after the Gmail label is applied."""

    def test_rejection_routes_to_rejection_branch(self) -> None:
        state = JobAppsState(message_id="m", sublabel="Rejection")
        assert router_after_apply(state) == "rejection"

    def test_offer_routes_to_offer_branch(self) -> None:
        state = JobAppsState(message_id="m", sublabel="Offer")
        assert router_after_apply(state) == "offer"

    def test_status_update_routes_to_terminal_branch(self) -> None:
        state = JobAppsState(message_id="m", sublabel="Status Update")
        assert router_after_apply(state) == "status_update"

    def test_application_confirmation_routes_to_app_conf_branch(self) -> None:
        state = JobAppsState(message_id="m", sublabel="Application Confirmation")
        assert router_after_apply(state) == "application_confirmation"

    @pytest.mark.parametrize(
        "sublabel",
        ["Interview Scheduling", "Assessment", "Recruiter Outreach"],
    )
    def test_actionable_sublabels_route_to_actionable_branch(self, sublabel: Sublabel) -> None:
        state = JobAppsState(message_id="m", sublabel=sublabel)
        assert router_after_apply(state) == "actionable"

    def test_node_error_after_apply_routes_to_error_branch(self) -> None:
        """A failed apply_sublabel routes to flag."""
        state = JobAppsState(message_id="m", sublabel="Rejection", terminal_reason="node_error")
        assert router_after_apply(state) == "error"


class TestRouterAfterMatch:
    """Branching after the match cascade."""

    def test_matched_routes_to_matched_branch(self) -> None:
        state = JobAppsState(
            message_id="m",
            match_result=MatchResult(status="matched", notion_row_id="row-1"),
        )
        assert router_after_match(state) == "matched"

    def test_ambiguous_routes_to_ambiguous_branch(self) -> None:
        state = JobAppsState(message_id="m", match_result=MatchResult(status="ambiguous"))
        assert router_after_match(state) == "ambiguous"

    def test_no_match_routes_to_no_match_branch(self) -> None:
        state = JobAppsState(message_id="m", match_result=MatchResult(status="no_match"))
        assert router_after_match(state) == "no_match"

    def test_node_error_routes_to_error_branch(self) -> None:
        state = JobAppsState(message_id="m", terminal_reason="node_error")
        assert router_after_match(state) == "error"

    def test_missing_match_result_routes_to_error_branch(self) -> None:
        state = JobAppsState(message_id="m")
        assert router_after_match(state) == "error"


# -------------------------------------------------------- integration tests


class TestGraphIntegration:
    """End-to-end runs through the compiled graph with canned node results."""

    def test_offer_flags_for_human_no_writes(self) -> None:
        """Offer flags for a human with no Notion match or write."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Offer"),
            ) as classifier,
            patch("services.job_apps.graph._apply_gmail_sublabel") as label_apply,
            patch("services.job_apps.graph.match_email_to_application") as matcher,
            patch("services.job_apps.graph.update_application") as writer,
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
        ):
            final = graph.invoke(JobAppsState(message_id="m-offer"))

        state = JobAppsState.model_validate(final)
        assert state.sublabel == "Offer"
        assert state.terminal_reason == "flagged_offer"
        # Offer gets its Gmail label, then flags for a human; no match or write.
        label_apply.assert_called_once()
        matcher.assert_not_called()
        writer.assert_not_called()
        classifier.assert_called_once()

    def test_status_update_terminates_with_no_action(self) -> None:
        """Status Update ends the run with no downstream calls."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Status Update"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel") as label_apply,
            patch("services.job_apps.graph.match_email_to_application") as matcher,
            patch("services.job_apps.graph.update_application") as writer,
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
        ):
            final = graph.invoke(JobAppsState(message_id="m-status"))

        state = JobAppsState.model_validate(final)
        assert state.sublabel == "Status Update"
        # No notion_row_id was set: no match step, no terminal_reason flagged.
        assert state.notion_row_id is None
        # Status Update gets its Gmail label, then ends (no match/write).
        label_apply.assert_called_once()
        matcher.assert_not_called()
        writer.assert_not_called()

    def test_rejection_matched_writes_notion_no_draft(self) -> None:
        """A matched rejection labels, matches, and writes Notion with no draft."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Rejection"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel") as label_apply,
            patch(
                "services.job_apps.graph.match_email_to_application",
                return_value=_match_cascade("matched", notion_row_id="row-rj"),
            ) as matcher,
            patch(
                "services.job_apps.graph._fetch_row_state",
                return_value=("Interview", {"Rejection Date": None}, None, None),
            ),
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
            patch(
                "services.job_apps.graph.update_application",
                return_value=WriteResult(status_changed=True, new_status="Rejected"),
            ) as writer,
        ):
            final = graph.invoke(JobAppsState(message_id="m-rejection"))

        state = JobAppsState.model_validate(final)
        assert state.sublabel == "Rejection"
        assert state.notion_row_id == "row-rj"
        assert state.notion_write_result is not None
        assert state.notion_write_result.new_status == "Rejected"
        assert state.terminal_reason is None
        # Rejection gets its Gmail label first, then matches and writes.
        label_apply.assert_called_once()
        matcher.assert_called_once()
        writer.assert_called_once()

    def test_application_confirmation_matched_writes_notion_no_draft(self) -> None:
        """A matched application confirmation writes Notion with no draft."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Application Confirmation"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel") as label_apply,
            patch(
                "services.job_apps.graph.match_email_to_application",
                return_value=_match_cascade("matched", notion_row_id="row-ac"),
            ),
            patch(
                "services.job_apps.graph._fetch_row_state",
                return_value=("Saved", {"Application Date": None}, None, None),
            ),
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
            patch(
                "services.job_apps.graph.update_application",
                return_value=WriteResult(status_changed=True, new_status="Applied"),
            ) as writer,
        ):
            final = graph.invoke(JobAppsState(message_id="m-appconf"))

        state = JobAppsState.model_validate(final)
        assert state.sublabel == "Application Confirmation"
        assert state.notion_row_id == "row-ac"
        assert state.notion_write_result is not None
        assert state.notion_write_result.new_status == "Applied"
        # Application Confirmation gets its Gmail label first, then writes.
        label_apply.assert_called_once()
        writer.assert_called_once()

    def test_rejection_unmatched_flags(self) -> None:
        """An unmatched rejection flags without writing."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Rejection"),
            ),
            # Rejection passes through apply_sublabel before match; patch
            # it so the real Gmail label CRUD doesn't run against the mock.
            patch("services.job_apps.graph._apply_gmail_sublabel"),
            patch(
                "services.job_apps.graph.match_email_to_application",
                return_value=_match_cascade("no_match"),
            ),
            patch("services.job_apps.graph.update_application") as writer,
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
        ):
            final = graph.invoke(JobAppsState(message_id="m-rj-nomatch"))

        state = JobAppsState.model_validate(final)
        assert state.terminal_reason == "flagged_no_match"
        writer.assert_not_called()

    def test_interview_scheduling_matched_full_path(self) -> None:
        """A matched actionable sublabel runs the full label, match, write path."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        match_cascade = _match_cascade("matched", notion_row_id="row-42")
        write_result = WriteResult(status_changed=True, new_status="Screen")

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Interview Scheduling"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel") as label_apply,
            patch(
                "services.job_apps.graph.match_email_to_application",
                return_value=match_cascade,
            ) as matcher,
            patch(
                "services.job_apps.graph._fetch_row_state",
                return_value=("Applied", {"Phone Screen Date": None}, None, None),
            ),
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
            patch(
                "services.job_apps.graph.update_application",
                return_value=write_result,
            ) as writer,
        ):
            final = graph.invoke(JobAppsState(message_id="m-interview"))

        state = JobAppsState.model_validate(final)
        assert state.sublabel == "Interview Scheduling"
        assert state.match_result is not None
        assert state.match_result.status == "matched"
        assert state.notion_row_id == "row-42"
        assert state.notion_write_result is not None
        assert state.notion_write_result.status_changed is True
        assert state.terminal_reason is None

        label_apply.assert_called_once()
        matcher.assert_called_once()
        writer.assert_called_once()

    def test_assessment_matched_full_path(self) -> None:
        """A matched assessment reaches the Notion write."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Assessment"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel"),
            patch(
                "services.job_apps.graph.match_email_to_application",
                return_value=_match_cascade("matched", notion_row_id="row-7"),
            ),
            patch(
                "services.job_apps.graph._fetch_row_state",
                return_value=("Applied", {"OA Date": None}, None, None),
            ),
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
            patch(
                "services.job_apps.graph.update_application",
                return_value=WriteResult(status_changed=True, new_status="Screen"),
            ) as writer,
        ):
            final = graph.invoke(JobAppsState(message_id="m-oa"))

        state = JobAppsState.model_validate(final)
        assert state.sublabel == "Assessment"
        assert state.notion_write_result is not None
        writer.assert_called_once()

    def test_recruiter_outreach_ambiguous_match_flags(self) -> None:
        """An ambiguous match flags without writing or drafting."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Recruiter Outreach"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel") as label_apply,
            patch(
                "services.job_apps.graph.match_email_to_application",
                return_value=_match_cascade("ambiguous", candidates=[{"id": "row-a"}]),
            ),
            patch("services.job_apps.graph.update_application") as writer,
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
        ):
            final = graph.invoke(JobAppsState(message_id="m-amb"))

        state = JobAppsState.model_validate(final)
        assert state.sublabel == "Recruiter Outreach"
        assert state.terminal_reason == "flagged_ambiguous"
        label_apply.assert_called_once()
        writer.assert_not_called()

    def test_actionable_no_match_flags(self) -> None:
        """An actionable sublabel with no match flags without writing."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Interview Scheduling"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel"),
            patch(
                "services.job_apps.graph.match_email_to_application",
                return_value=_match_cascade("no_match"),
            ),
            patch("services.job_apps.graph.update_application") as writer,
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
        ):
            final = graph.invoke(JobAppsState(message_id="m-nomatch"))

        state = JobAppsState.model_validate(final)
        assert state.terminal_reason == "flagged_no_match"
        writer.assert_not_called()

    @pytest.mark.parametrize(
        "sublabel",
        [
            "Rejection",
            "Offer",
            "Status Update",
            "Application Confirmation",
            "Interview Scheduling",
            "Assessment",
            "Recruiter Outreach",
        ],
    )
    def test_apply_sublabel_invoked_for_every_sublabel(self, sublabel: Sublabel) -> None:
        """All seven sublabels get their Gmail label applied."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification(sublabel),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel") as label_apply,
            patch(
                "services.job_apps.graph.match_email_to_application",
                return_value=_match_cascade("no_match"),
            ),
            patch("services.job_apps.graph.update_application"),
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
        ):
            graph.invoke(JobAppsState(message_id="m-all7"))

        label_apply.assert_called_once()
        assert label_apply.call_args.kwargs["sublabel"] == sublabel

    def test_classifier_failure_caught_no_propagation(self) -> None:
        """A classifier raise is captured in state instead of propagating."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                side_effect=RuntimeError("anthropic 500"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel"),
            patch("services.job_apps.graph.match_email_to_application") as matcher,
            patch("services.job_apps.graph.update_application") as writer,
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
        ):
            final = graph.invoke(JobAppsState(message_id="m-err"))

        state = JobAppsState.model_validate(final)
        assert state.sublabel is None
        assert state.terminal_reason == "node_error"
        assert any("classify_sublabel" in e for e in state.errors)
        # The error path goes to the flag node; downstream nodes never run.
        matcher.assert_not_called()
        writer.assert_not_called()

    def test_match_failure_caught_routes_to_flag(self) -> None:
        """A match raise is captured in state and routes to flag."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Interview Scheduling"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel"),
            patch(
                "services.job_apps.graph.match_email_to_application",
                side_effect=RuntimeError("ntn timeout"),
            ),
            patch("services.job_apps.graph.update_application") as writer,
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
        ):
            final = graph.invoke(JobAppsState(message_id="m-match-err"))

        state = JobAppsState.model_validate(final)
        assert state.terminal_reason == "node_error"
        assert any("match" in e and "ntn timeout" in e for e in state.errors)
        writer.assert_not_called()

    def test_parse_email_extracts_urls_from_raw_html_part(self) -> None:
        """Anchor-only posting links survive into parsed_email.urls."""
        import base64

        from services.job_apps.graph import parse_email_node

        posting_url = "https://boards.greenhouse.io/acme/jobs/123"
        plain = "Apply via the link in this email."
        html = f'<p>Apply: <a href="{posting_url}">here</a></p>'
        message = {
            "id": "m-html",
            "threadId": "t-html",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "From", "value": "Recruiter <recruiter@bigco.com>"},
                    {"name": "Subject", "value": "Your application"},
                    {"name": "Date", "value": "Tue, 05 May 2026 12:00:00 +0000"},
                ],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": base64.urlsafe_b64encode(plain.encode()).decode("ascii")},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"data": base64.urlsafe_b64encode(html.encode()).decode("ascii")},
                    },
                ],
            },
        }
        service = MagicMock()
        service.users.return_value.messages.return_value.get.return_value.execute.return_value = (
            message
        )

        update = parse_email_node(JobAppsState(message_id="m-html"), service=service)

        parsed = update["parsed_email"]
        assert parsed.urls == [posting_url]
        # body_text still comes from the text/plain part.
        assert parsed.body_text == plain

    def test_parse_email_leaves_urls_empty_for_plain_text_only(self) -> None:
        """Plain-text-only mail leaves urls empty for the matcher's regex fallback."""
        from services.job_apps.graph import parse_email_node

        service = _gmail_service_with_message(
            body_text="Apply at https://boards.greenhouse.io/acme/jobs/123"
        )
        update = parse_email_node(JobAppsState(message_id="m-1"), service=service)
        assert update["parsed_email"].urls == []

    def test_parse_email_propagates_exceptions_through_invoke(self) -> None:
        """parse_email exceptions propagate through invoke() for HTTP mapping."""
        service = MagicMock()
        service.users.return_value.messages.return_value.get.return_value.execute.side_effect = (
            RuntimeError("gmail 500")
        )
        graph = _build_test_graph(service=service)

        with (
            patch("services.job_apps.graph.classify_sublabel") as classifier,
            patch("services.job_apps.graph.match_email_to_application") as matcher,
            pytest.raises(RuntimeError, match="gmail 500"),
        ):
            graph.invoke(JobAppsState(message_id="m-parse-err"))

        classifier.assert_not_called()
        matcher.assert_not_called()


# ----------------------------------------------------------- _fetch_row_state


class TestFetchRowState:
    """Reads a row's current Status + date fields for the writer's guards."""

    def test_parses_status_dates_notes_archive_reason_from_ntn_response(self) -> None:
        """A successful ntn response parses into status, dates, notes, and archive reason.

        Status is read from the select key; an earlier version read the
        status key and silently defaulted every row to "Saved".
        """
        from services.job_apps.graph import _fetch_row_state

        ntn_payload = {
            "properties": {
                "Status": {"select": {"name": "Applied"}},
                "Application Date": {"date": {"start": "2026-04-01"}},
                "Phone Screen Date": {"date": None},
                "OA Date": {"date": {"start": "2026-04-15T00:00:00.000Z"}},
                "Notes": {
                    "rich_text": [
                        {"plain_text": "Existing notes line 1. "},
                        {"plain_text": "Line 2."},
                    ]
                },
                "Archive Reason": {"rich_text": []},
            }
        }
        proc_result = MagicMock(returncode=0, stdout=json.dumps(ntn_payload))
        with patch("shared.ntn.subprocess.run", return_value=proc_result):
            status, dates, current_notes, current_archive_reason = _fetch_row_state("row-1")

        assert status == "Applied"
        assert dates["Application Date"] == date(2026, 4, 1)
        assert dates["Phone Screen Date"] is None
        # A date with a time suffix is still parsed via the [:10] slice.
        assert dates["OA Date"] == date(2026, 4, 15)
        assert current_notes == "Existing notes line 1. Line 2."
        assert current_archive_reason is None

    def test_missing_status_defaults_to_saved(self) -> None:
        """A row with no Status property still returns a parseable default."""
        from services.job_apps.graph import _fetch_row_state

        proc_result = MagicMock(returncode=0, stdout=json.dumps({"properties": {}}))
        with patch("shared.ntn.subprocess.run", return_value=proc_result):
            status, _, current_notes, current_archive_reason = _fetch_row_state("row-1")
        assert status == "Saved"
        assert current_notes is None
        assert current_archive_reason is None

    def test_ntn_failure_raises_ntn_error(self) -> None:
        """A non-zero ntn exit raises NtnError for the node to capture."""
        from services.job_apps.graph import _fetch_row_state
        from shared.ntn import NtnError

        proc_result = MagicMock(returncode=1, stderr="forbidden", stdout="")
        with (
            patch("shared.ntn.subprocess.run", return_value=proc_result),
            pytest.raises(NtnError, match="ntn failed"),
        ):
            _fetch_row_state("row-1")


# ------------------------------------------------------------ observability


class TestGraphObservability:
    """Verify node_timings and token_usage_by_node are populated."""

    def test_happy_path_populates_node_timings_and_token_usage(self) -> None:
        """A full matched run sets per-node timings and token usage."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Interview Scheduling"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel"),
            patch(
                "services.job_apps.graph.match_email_to_application",
                return_value=_match_cascade("matched", notion_row_id="row-1"),
            ),
            patch(
                "services.job_apps.graph._fetch_row_state",
                return_value=("Applied", {"Phone Screen Date": None}, None, None),
            ),
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
            patch(
                "services.job_apps.graph.update_application",
                return_value=WriteResult(status_changed=True, new_status="Screen"),
            ),
        ):
            final = graph.invoke(JobAppsState(message_id="m-obs"))

        state = JobAppsState.model_validate(final)
        # Every node on the actionable + matched path recorded its latency.
        for node_name in (
            "parse_email",
            "classify_sublabel",
            "apply_sublabel",
            "match",
            "extract",
            "notion_write",
        ):
            assert node_name in state.node_timings, f"{node_name} missing from node_timings"
            assert state.node_timings[node_name] >= 0
        # Three LLM-calling nodes each contributed a token bucket.
        assert set(state.token_usage_by_node.keys()) == {
            "classify_sublabel",
            "match",
            "extract",
        }
        assert state.token_usage_by_node["classify_sublabel"]["input"] == 100
        assert state.token_usage_by_node["match"]["input"] == 300
        assert state.token_usage_by_node["extract"]["input"] == 400
        # model_used was captured (all three nodes report the same model).
        assert state.model_used == "claude-opus-4-7"

    def test_url_match_short_circuit_omits_token_usage_for_match(self) -> None:
        """A URL-match run leaves no match entry in token_usage_by_node."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Application Confirmation"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel"),
            patch(
                "services.job_apps.graph.match_email_to_application",
                return_value=_match_cascade("matched", notion_row_id="row-1", with_llm=False),
            ),
            patch(
                "services.job_apps.graph._fetch_row_state",
                return_value=("Saved", {"Application Date": None}, None, None),
            ),
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
            patch(
                "services.job_apps.graph.update_application",
                return_value=WriteResult(status_changed=True, new_status="Applied"),
            ),
        ):
            final = graph.invoke(JobAppsState(message_id="m-url-only"))

        state = JobAppsState.model_validate(final)
        # The match node ran (so timing is recorded) but never invoked the LLM.
        assert "match" in state.node_timings
        assert "match" not in state.token_usage_by_node

    def test_classifier_error_still_records_timing(self) -> None:
        """A classifier raise still records classify_sublabel timing."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                side_effect=RuntimeError("anthropic 500"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel"),
            patch("services.job_apps.graph.match_email_to_application"),
            patch("services.job_apps.graph.update_application"),
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
        ):
            final = graph.invoke(JobAppsState(message_id="m-err-timing"))

        state = JobAppsState.model_validate(final)
        assert "classify_sublabel" in state.node_timings
        # No token usage on the error path.
        assert "classify_sublabel" not in state.token_usage_by_node

    def test_status_changed_preserved_on_state_when_writer_blocked(self) -> None:
        """A WriteResult with status_changed=False flows through to final state."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Interview Scheduling"),
            ),
            patch("services.job_apps.graph._apply_gmail_sublabel"),
            patch(
                "services.job_apps.graph.match_email_to_application",
                return_value=_match_cascade("matched", notion_row_id="row-1"),
            ),
            patch(
                "services.job_apps.graph._fetch_row_state",
                return_value=("Interview", {"Phone Screen Date": None}, None, None),
            ),
            patch("services.job_apps.graph.extract_email_metadata", return_value=_extraction()),
            patch(
                "services.job_apps.graph.update_application",
                return_value=WriteResult(status_changed=False, new_status=None),
            ),
        ):
            final = graph.invoke(JobAppsState(message_id="m-no-change"))

        state = JobAppsState.model_validate(final)
        assert state.notion_write_result is not None
        assert state.notion_write_result.status_changed is False
        assert state.notion_write_result.new_status is None

    def test_offer_path_sets_flagged_offer_terminal_reason(self) -> None:
        """The offer branch sets terminal_reason to flagged_offer."""
        service = _gmail_service_with_message()
        graph = _build_test_graph(service=service)

        with (
            patch(
                "services.job_apps.graph.classify_sublabel",
                return_value=_classification("Offer"),
            ),
            # Offer passes through apply_sublabel before flagging.
            patch("services.job_apps.graph._apply_gmail_sublabel"),
        ):
            final = graph.invoke(JobAppsState(message_id="m-offer-event"))

        state = JobAppsState.model_validate(final)
        assert state.terminal_reason == "flagged_offer"
