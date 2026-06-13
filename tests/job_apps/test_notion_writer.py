"""Tests for the Notion writer and its hard-rule guards.

Subprocess is mocked; each test captures the PATCH args and JSON body
so assertions cover both the writer's decisions and the payload.
"""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from services.job_apps.notion_writer import (
    PROP_APPLICATION_DATE,
    PROP_OA_DATE,
    PROP_OFFER_DATE,
    PROP_ONSITE_DATE,
    PROP_PHONE_SCREEN_DATE,
    PROP_RECRUITER_SCREEN_DATE,
    PROP_REJECTION_DATE,
    PROP_STATUS,
    REJECTION_EXCERPT_MAX,
    _build_properties,
    _guard_monotonic,
    _guard_no_overwrite_dates,
    _guard_terminal,
    email_utc_to_et_date,
    update_application,
)
from shared.ntn import NtnError
from tests.conftest import FakeProc

if TYPE_CHECKING:
    from services.job_apps.types import Status, Sublabel, WriteResult


# ---------------------------------------------------------------------- helpers


def _ok(page_id: str = "page-abc") -> str:
    return json.dumps({"object": "page", "id": page_id})


def _capture_run(captured: list[dict[str, Any]]) -> Any:
    def _run(args: list[str], **kwargs: Any) -> FakeProc:
        # args layout: ["ntn", "api", "v1/pages/<id>", "-X", "PATCH", "-d", "<json>"].
        body: dict[str, Any] = {}
        if len(args) >= 7 and args[5] == "-d":
            body = json.loads(args[6])
        captured.append({"args": args, "body": body, "kwargs": kwargs})
        proc = FakeProc(_ok())
        proc.args = args
        return proc

    return _run


def _run_writer(
    captured: list[dict[str, Any]],
    *,
    sublabel: Sublabel,
    current_status: Status,
    email_date: date = date(2026, 5, 4),
    email_metadata: dict[str, Any] | None = None,
    current_dates: dict[str, date | None] | None = None,
    notion_row_id: str = "row-1",
) -> WriteResult:
    fake = _capture_run(captured)
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        return update_application(
            notion_row_id=notion_row_id,
            sublabel=sublabel,
            email_received_at_et=email_date,
            email_metadata=email_metadata or {},
            current_status=current_status,
            current_dates=current_dates or {},
        )


# ---------------------------------------------------------- guard 1: terminal


@pytest.mark.parametrize("status", ["Rejected", "Withdrawn", "Archived"])
def test_terminal_status_blocks_all_writes(status: Status) -> None:
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Application Confirmation",
        current_status=status,
    )
    assert result.errored is True
    assert result.error_msg == "terminal status"
    assert result.status_changed is False
    assert result.dates_set == {}
    assert captured == []  # No PATCH was issued.


def test_guard_terminal_helper_returns_none_for_non_terminal() -> None:
    non_terminal: tuple[Status, ...] = ("Saved", "Applied", "Screen", "Interview", "Offer")
    for status in non_terminal:
        assert _guard_terminal(status) is None


# ---------------------------------------------------------- guard 2: monotonic


def test_status_regression_is_rejected() -> None:
    """The monotonic guard rejects a backward status proposal."""
    # Construct the regression directly via the guard helper for clarity.
    blocked = _guard_monotonic("Applied", "Saved")
    assert blocked is not None
    assert blocked.errored is True
    assert blocked.error_msg is not None
    assert "Saved" in blocked.error_msg
    assert "Applied" in blocked.error_msg


@pytest.mark.parametrize("current_status", ["Interview", "Offer"])
def test_interview_scheduling_hard_fails_when_row_is_past_screen(
    current_status: Status,
) -> None:
    """A screen-round email against a row past Screen hard-fails the write.

    Interview Scheduling with a known round always proposes a status, so
    the monotonic guard rejects the entire write, date included, and no
    PATCH is issued.
    """
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Interview Scheduling",
        current_status=current_status,
        email_metadata={"interview_round": "phone_screen"},
        current_dates={PROP_PHONE_SCREEN_DATE: None},
    )
    assert result.errored is True
    assert result.error_msg == f"status regression: {current_status} → Screen"
    assert result.status_changed is False
    assert result.dates_set == {}
    assert captured == []  # No PATCH was issued.


def test_guard_monotonic_allows_no_op_same_status() -> None:
    assert _guard_monotonic("Applied", "Applied") is None


def test_guard_monotonic_allows_forward_edge() -> None:
    assert _guard_monotonic("Applied", "Screen") is None


# ----------------------------------------------------- guard 3: no-overwrite dates


def test_guard_no_overwrite_drops_existing_date_keys() -> None:
    current = {
        PROP_APPLICATION_DATE: date(2026, 4, 1),
        PROP_PHONE_SCREEN_DATE: None,
    }
    proposed = {
        PROP_APPLICATION_DATE: date(2026, 5, 4),
        PROP_PHONE_SCREEN_DATE: date(2026, 5, 10),
    }
    filtered, skipped = _guard_no_overwrite_dates(current, proposed)
    assert filtered == {PROP_PHONE_SCREEN_DATE: date(2026, 5, 10)}
    assert skipped == [PROP_APPLICATION_DATE]


def test_existing_date_skipped_and_recorded_in_result() -> None:
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Application Confirmation",
        current_status="Saved",
        email_date=date(2026, 5, 4),
        current_dates={PROP_APPLICATION_DATE: date(2026, 4, 17)},
    )
    assert PROP_APPLICATION_DATE in result.dates_skipped
    assert PROP_APPLICATION_DATE not in result.dates_set
    # Status is allowed to change even when the date was skipped.
    assert result.status_changed is True
    assert result.new_status == "Applied"


# --------------------------------------------------------- sublabel happy paths


def test_build_properties_serializes_status_as_select_not_status() -> None:
    """The Status property serializes as select, never status.

    The live column is type select; a status-typed value 400s every
    status-changing PATCH, and the error is swallowed as a node_error.
    """
    props = _build_properties(
        new_status="Applied",
        dates_to_write={},
        notes_append=None,
        archive_reason_append=None,
        current_notes=None,
        current_archive_reason=None,
    )
    assert props[PROP_STATUS] == {"select": {"name": "Applied"}}
    assert "status" not in props[PROP_STATUS]


def test_offer_advances_status_and_sets_offer_date_and_appends_summary() -> None:
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Offer",
        current_status="Interview",
        email_date=date(2026, 5, 4),
        email_metadata={"offer_summary": "Base 200k, 100k equity, 30k sign-on"},
    )
    assert result.errored is False
    assert result.status_changed is True
    assert result.new_status == "Offer"
    assert result.dates_set == {PROP_OFFER_DATE: date(2026, 5, 4)}
    body = captured[0]["body"]
    assert body["properties"][PROP_STATUS] == {"select": {"name": "Offer"}}
    assert body["properties"][PROP_OFFER_DATE] == {"date": {"start": "2026-05-04"}}
    notes = body["properties"]["Notes"]["rich_text"][0]["text"]["content"]
    assert "Base 200k" in notes


def test_interview_scheduling_phone_screen_sets_screen_status_and_phone_screen_date() -> None:
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Interview Scheduling",
        current_status="Applied",
        email_date=date(2026, 5, 4),
        email_metadata={"interview_round": "phone_screen"},
    )
    assert result.status_changed is True
    assert result.new_status == "Screen"
    assert result.dates_set == {PROP_PHONE_SCREEN_DATE: date(2026, 5, 4)}


def test_interview_scheduling_onsite_sets_interview_status_and_onsite_date() -> None:
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Interview Scheduling",
        current_status="Screen",
        email_date=date(2026, 5, 4),
        email_metadata={"interview_round": "onsite"},
    )
    assert result.status_changed is True
    assert result.new_status == "Interview"
    assert result.dates_set == {PROP_ONSITE_DATE: date(2026, 5, 4)}


def test_interview_scheduling_recruiter_call_sets_screen_and_recruiter_screen_date() -> None:
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Interview Scheduling",
        current_status="Saved",
        email_date=date(2026, 5, 4),
        email_metadata={"interview_round": "recruiter_screen"},
    )
    assert result.status_changed is True
    assert result.new_status == "Screen"
    assert result.dates_set == {PROP_RECRUITER_SCREEN_DATE: date(2026, 5, 4)}


def test_assessment_advances_only_from_saved_or_applied() -> None:
    captured_a: list[dict[str, Any]] = []
    result_a = _run_writer(
        captured_a,
        sublabel="Assessment",
        current_status="Applied",
        email_date=date(2026, 5, 4),
        email_metadata={
            "assessment_platform": "HackerRank",
            "assessment_deadline_str": "Due 2026-05-12",
        },
    )
    assert result_a.status_changed is True
    assert result_a.new_status == "Screen"
    assert result_a.dates_set == {PROP_OA_DATE: date(2026, 5, 4)}

    captured_b: list[dict[str, Any]] = []
    result_b = _run_writer(
        captured_b,
        sublabel="Assessment",
        current_status="Interview",
        email_date=date(2026, 5, 4),
        email_metadata={"assessment_platform": "HackerRank"},
    )
    # Status must not regress to Screen from Interview.
    assert result_b.status_changed is False
    # OA date is still patched onto the row (informational write).
    assert result_b.dates_set == {PROP_OA_DATE: date(2026, 5, 4)}


def test_assessment_uses_explicit_deadline_when_provided() -> None:
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Assessment",
        current_status="Applied",
        email_date=date(2026, 5, 4),
        email_metadata={
            "assessment_platform": "HackerRank",
            "assessment_deadline_date": date(2026, 5, 12),
        },
    )
    # Deadline beats email-received-at when both are provided.
    assert result.dates_set == {PROP_OA_DATE: date(2026, 5, 12)}


def test_recruiter_outreach_advances_only_from_saved_or_applied() -> None:
    captured_a: list[dict[str, Any]] = []
    result_a = _run_writer(
        captured_a,
        sublabel="Recruiter Outreach",
        current_status="Saved",
        email_metadata={"recruiter_name": "Pat Smith", "recruiter_email": "pat@acme.com"},
    )
    assert result_a.status_changed is True
    assert result_a.new_status == "Screen"

    captured_b: list[dict[str, Any]] = []
    result_b = _run_writer(
        captured_b,
        sublabel="Recruiter Outreach",
        current_status="Interview",
        email_metadata={"recruiter_name": "Pat Smith"},
    )
    assert result_b.status_changed is False


def test_recruiter_outreach_writes_proposed_screen_date_when_present() -> None:
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Recruiter Outreach",
        current_status="Saved",
        email_metadata={
            "recruiter_name": "Pat Smith",
            "proposed_screen_date": date(2026, 5, 10),
        },
    )
    assert result.dates_set == {PROP_RECRUITER_SCREEN_DATE: date(2026, 5, 10)}


def test_status_update_is_a_no_op() -> None:
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Status Update",
        current_status="Screen",
    )
    assert result.errored is False
    assert result.status_changed is False
    assert result.dates_set == {}
    assert captured == []  # no PATCH issued


def test_application_confirmation_advances_only_from_saved() -> None:
    captured_a: list[dict[str, Any]] = []
    result_a = _run_writer(
        captured_a,
        sublabel="Application Confirmation",
        current_status="Saved",
        email_date=date(2026, 5, 4),
    )
    assert result_a.status_changed is True
    assert result_a.new_status == "Applied"
    assert result_a.dates_set == {PROP_APPLICATION_DATE: date(2026, 5, 4)}

    captured_b: list[dict[str, Any]] = []
    result_b = _run_writer(
        captured_b,
        sublabel="Application Confirmation",
        current_status="Screen",
        email_date=date(2026, 5, 4),
    )
    # Already past Applied; must not regress.
    assert result_b.status_changed is False
    # Application Date is still patched if it was empty.
    assert result_b.dates_set == {PROP_APPLICATION_DATE: date(2026, 5, 4)}


def test_rejection_sets_rejected_and_writes_truncated_excerpt() -> None:
    long_excerpt = "Unfortunately, we have decided to move forward with other candidates. " * 5
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Rejection",
        current_status="Interview",
        email_date=date(2026, 5, 4),
        email_metadata={"rejection_excerpt": long_excerpt},
    )
    assert result.status_changed is True
    assert result.new_status == "Rejected"
    assert result.dates_set == {PROP_REJECTION_DATE: date(2026, 5, 4)}
    body = captured[0]["body"]
    archive_text = body["properties"]["Archive Reason"]["rich_text"][0]["text"]["content"]
    assert len(archive_text) <= REJECTION_EXCERPT_MAX
    # Truncation appends an ellipsis.
    assert archive_text.endswith("…")


def test_rejection_skips_excerpt_when_metadata_missing() -> None:
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Rejection",
        current_status="Interview",
        email_date=date(2026, 5, 4),
        email_metadata={},
    )
    body = captured[0]["body"]
    assert "Archive Reason" not in body["properties"]
    assert result.new_status == "Rejected"


# ------------------------------------------------------------- UTC → ET helper


def test_utc_to_et_date_converts_late_night_to_previous_day() -> None:
    # 2026-04-20T01:27Z is 2026-04-19 21:27 ET (DST in effect; UTC-4).
    assert email_utc_to_et_date("2026-04-20T01:27Z") == date(2026, 4, 19)


def test_utc_to_et_date_handles_offset_form() -> None:
    # Same instant but in +00:00 form.
    assert email_utc_to_et_date("2026-04-20T01:27:00+00:00") == date(2026, 4, 19)


def test_utc_to_et_date_morning_is_same_day() -> None:
    # 2026-05-04T14:00Z is 10:00 ET, the same calendar day.
    assert email_utc_to_et_date("2026-05-04T14:00Z") == date(2026, 5, 4)


# --------------------------------------------------------- merged-text behavior


def test_notes_append_merges_with_existing_notes() -> None:
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Offer",
        current_status="Interview",
        email_metadata={
            "offer_summary": "Base 200k",
            "current_notes": "Recruiter said decision by Friday.",
        },
    )
    body = captured[0]["body"]
    notes = body["properties"]["Notes"]["rich_text"][0]["text"]["content"]
    assert "Recruiter said decision by Friday." in notes
    assert "Base 200k" in notes
    # Paragraph break between existing and new content.
    assert "\n\n" in notes
    assert result.notes_appended == "Base 200k"


def test_no_op_with_no_dates_and_no_status_change_does_not_call_ntn() -> None:
    """Status Update against an Interview row writes nothing at all."""
    captured: list[dict[str, Any]] = []
    result = _run_writer(
        captured,
        sublabel="Status Update",
        current_status="Interview",
    )
    assert captured == []
    assert result.errored is False
    assert result.status_changed is False


# ---------------------------------------------------------- subprocess plumbing


def test_writer_invokes_ntn_with_patch_method_and_pages_path() -> None:
    captured: list[dict[str, Any]] = []
    _run_writer(
        captured,
        sublabel="Application Confirmation",
        current_status="Saved",
        notion_row_id="row-xyz-7",
    )
    args = captured[0]["args"]
    assert args[0] == "ntn"
    assert args[1] == "api"
    assert args[2] == "v1/pages/row-xyz-7"
    assert args[3] == "-X"
    assert args[4] == "PATCH"
    assert args[5] == "-d"


def test_writer_propagates_notion_token_to_subprocess_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared.settings import get_settings

    monkeypatch.setenv("NOTION_TOKEN", "secret_writer_token")
    get_settings.cache_clear()
    captured: list[dict[str, Any]] = []
    try:
        _run_writer(
            captured,
            sublabel="Application Confirmation",
            current_status="Saved",
        )
    finally:
        get_settings.cache_clear()
    env = captured[0]["kwargs"]["env"]
    assert env["NOTION_API_TOKEN"] == "secret_writer_token"


def test_writer_raises_on_ntn_nonzero_exit() -> None:
    def _failing_run(args: list[str], **kwargs: Any) -> FakeProc:
        return FakeProc(stdout="", stderr="ntn: 401 unauthorized", returncode=1)

    with (
        patch("shared.ntn.subprocess.run", side_effect=_failing_run),
        pytest.raises(NtnError) as excinfo,
    ):
        update_application(
            notion_row_id="row-1",
            sublabel="Application Confirmation",
            email_received_at_et=date(2026, 5, 4),
            email_metadata={},
            current_status="Saved",
            current_dates={},
        )
    err = excinfo.value
    assert err.returncode == 1
    assert "401 unauthorized" in err.stderr
