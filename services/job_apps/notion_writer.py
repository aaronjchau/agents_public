"""Patch a single row in the Notion Job Applications DB.

The only pipeline component that mutates Notion. Each hard rule (no
terminal writes, no status regressions, no date overwrites) is an
explicit guard with its own tests; the writer never reads, so callers
pre-fetch row state and one PATCH carries the merged content.
Design notes: docs/design.md.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from services.job_apps.state_machine import (
    TERMINAL_STATUSES,
    is_valid_transition,
)
from services.job_apps.types import WriteResult
from shared.ntn import run_ntn

if TYPE_CHECKING:
    from services.job_apps.types import Status, Sublabel

ET = ZoneInfo("America/New_York")

# Notion property names. Must match the Job Applications database schema
# exactly; renaming a column in Notion breaks this writer until the
# constant is updated.
PROP_STATUS = "Status"
PROP_NOTES = "Notes"
PROP_ARCHIVE_REASON = "Archive Reason"
PROP_APPLICATION_DATE = "Application Date"
PROP_PHONE_SCREEN_DATE = "Phone Screen Date"
PROP_RECRUITER_SCREEN_DATE = "Recruiter Screen Date"
PROP_ONSITE_DATE = "Onsite Date"
PROP_OFFER_DATE = "Offer Date"
PROP_OA_DATE = "OA Date"
PROP_REJECTION_DATE = "Rejection Date"

# Cap on the rejection excerpt that lands in Archive Reason; longer text
# is truncated with an ellipsis.
REJECTION_EXCERPT_MAX = 100


# ----------------------------------------------------------------------- helpers


def email_utc_to_et_date(utc_iso_string: str) -> date:
    """Convert an ISO-8601 UTC timestamp into the ET calendar date.

    Accepts both Z and +00:00 suffix forms. RFC 2822 Date headers should
    be parsed via email.utils.parsedate_to_datetime first and passed in
    as isoformat().
    """
    s = utc_iso_string.strip()
    # fromisoformat accepts the Z suffix in 3.11+; the explicit replace
    # keeps the helper portable to older parsers.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.astimezone(ET).date()


# ------------------------------------------------------------------- sublabel map


def _actions_for_sublabel(
    *,
    sublabel: Sublabel,
    email_received_at_et: date,
    email_metadata: dict[str, Any],
    current_status: Status,
) -> tuple[Status | None, dict[str, date], str | None, str | None]:
    """Resolve a sublabel into (proposed_status, dates_to_write, notes_append, archive_reason_append).

    A None proposed_status means the sublabel does not change status,
    either inherently (Status Update) or because the row is already past
    the subset of statuses the sublabel advances from.
    """
    if sublabel == "Offer":
        proposed: Status | None = "Offer"
        offer_summary = _coerce_str(email_metadata.get("offer_summary"))
        return (
            proposed,
            {PROP_OFFER_DATE: email_received_at_et},
            offer_summary,
            None,
        )

    if sublabel == "Interview Scheduling":
        # Unlike Assessment and Recruiter Outreach (which soft-skip by
        # proposing None when the row is already past Screen), a known
        # round always proposes a status, so scheduling an earlier round
        # than the row's state hard-fails the whole write (date included)
        # via _guard_monotonic, flagging the inconsistency for a human
        # instead of silently recording a backdated round.
        round_kind = email_metadata.get("interview_round")
        if round_kind == "recruiter_screen":
            return (
                "Screen",
                {PROP_RECRUITER_SCREEN_DATE: email_received_at_et},
                None,
                None,
            )
        if round_kind == "phone_screen":
            return (
                "Screen",
                {PROP_PHONE_SCREEN_DATE: email_received_at_et},
                None,
                None,
            )
        if round_kind == "onsite":
            return (
                "Interview",
                {PROP_ONSITE_DATE: email_received_at_et},
                None,
                None,
            )
        # Unknown round: write nothing rather than fabricate one.
        return (None, {}, None, None)

    if sublabel == "Assessment":
        assessment_status: Status | None = (
            "Screen" if current_status in {"Saved", "Applied"} else None
        )
        deadline = email_metadata.get("assessment_deadline_date")
        oa_date = deadline if isinstance(deadline, date) else email_received_at_et
        platform = _coerce_str(email_metadata.get("assessment_platform"))
        deadline_str = _coerce_str(email_metadata.get("assessment_deadline_str"))
        notes = _join_present(["Assessment", platform, deadline_str], sep=" — ")
        return assessment_status, {PROP_OA_DATE: oa_date}, notes, None

    if sublabel == "Recruiter Outreach":
        recruiter_status: Status | None = (
            "Screen" if current_status in {"Saved", "Applied"} else None
        )
        proposed_screen = email_metadata.get("proposed_screen_date")
        dates: dict[str, date] = {}
        if isinstance(proposed_screen, date):
            dates[PROP_RECRUITER_SCREEN_DATE] = proposed_screen
        recruiter_name = _coerce_str(email_metadata.get("recruiter_name"))
        recruiter_email = _coerce_str(email_metadata.get("recruiter_email"))
        notes = _join_present(["Recruiter outreach", recruiter_name, recruiter_email], sep=" — ")
        return recruiter_status, dates, notes, None

    if sublabel == "Status Update":
        return (None, {}, None, None)

    if sublabel == "Application Confirmation":
        confirmation_status: Status | None = "Applied" if current_status == "Saved" else None
        return (
            confirmation_status,
            {PROP_APPLICATION_DATE: email_received_at_et},
            None,
            None,
        )

    if sublabel == "Rejection":
        excerpt = _coerce_str(email_metadata.get("rejection_excerpt"))
        truncated = _truncate(excerpt, REJECTION_EXCERPT_MAX) if excerpt else None
        return (
            "Rejected",
            {PROP_REJECTION_DATE: email_received_at_et},
            None,
            truncated,
        )

    # typing.Literal makes this unreachable, but mypy needs the explicit
    # exhaustive fallthrough.
    raise ValueError(f"unknown sublabel: {sublabel}")  # pragma: no cover


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _truncate(value: str, maxlen: int) -> str:
    if len(value) <= maxlen:
        return value
    # Reserve one char for the ellipsis so the visible width stays at maxlen.
    return value[: maxlen - 1].rstrip() + "…"


def _join_present(parts: list[str | None], *, sep: str) -> str | None:
    cleaned = [p for p in parts if p]
    if not cleaned:
        return None
    return sep.join(cleaned)


# -------------------------------------------------------------------- guards


def _guard_terminal(current_status: Status) -> WriteResult | None:
    """Refuse all writes when the row is in a terminal status.

    Returns None when the row is writable, a fully formed error
    WriteResult otherwise.
    """
    if current_status in TERMINAL_STATUSES:
        return WriteResult(errored=True, error_msg="terminal status")
    return None


def _guard_monotonic(current_status: Status, proposed_status: Status) -> WriteResult | None:
    """Refuse a status write that would move the row backward.

    Returns None when the transition is valid, an error WriteResult
    otherwise; same-status no-ops are valid via is_valid_transition.
    """
    if is_valid_transition(current_status, proposed_status):
        return None
    return WriteResult(
        errored=True,
        error_msg=f"status regression: {current_status} → {proposed_status}",
    )


def _guard_no_overwrite_dates(
    current_dates: dict[str, date | None],
    dates_to_write: dict[str, date],
) -> tuple[dict[str, date], list[str]]:
    """Drop date writes whose current value is already non-empty.

    Returns (filtered_writes, skipped_keys) so the caller can record the
    skips in its WriteResult.
    """
    filtered: dict[str, date] = {}
    skipped: list[str] = []
    for key, value in dates_to_write.items():
        existing = current_dates.get(key)
        if existing is not None:
            skipped.append(key)
        else:
            filtered[key] = value
    return filtered, skipped


# ------------------------------------------------------------------ public API


def update_application(
    *,
    notion_row_id: str,
    sublabel: Sublabel,
    email_received_at_et: date,
    email_metadata: dict[str, Any],
    current_status: Status,
    current_dates: dict[str, date | None],
) -> WriteResult:
    """Patch a Job-Apps row according to the sublabel's effect.

    The caller supplies the row's current status and date snapshot and
    converts email_received_at_et via email_utc_to_et_date; the writer
    never fabricates a date. Returns a WriteResult summarizing what
    changed, with errored=True when a hard-rule guard rejected the
    write; the PATCH is only issued when at least one non-skipped field
    remains.
    """
    terminal_block = _guard_terminal(current_status)
    if terminal_block is not None:
        return terminal_block

    proposed_status, dates_to_write, notes_append, archive_reason_append = _actions_for_sublabel(
        sublabel=sublabel,
        email_received_at_et=email_received_at_et,
        email_metadata=email_metadata,
        current_status=current_status,
    )

    if proposed_status is not None:
        regression = _guard_monotonic(current_status, proposed_status)
        if regression is not None:
            return regression

    filtered_dates, skipped = _guard_no_overwrite_dates(current_dates, dates_to_write)

    status_changed = proposed_status is not None and proposed_status != current_status
    new_status: Status | None = proposed_status if status_changed else None

    properties = _build_properties(
        new_status=new_status,
        dates_to_write=filtered_dates,
        notes_append=notes_append,
        archive_reason_append=archive_reason_append,
        current_notes=_coerce_str(email_metadata.get("current_notes")),
        current_archive_reason=_coerce_str(email_metadata.get("current_archive_reason")),
    )

    if not properties:
        # Nothing to write; return a no-op result rather than paying for
        # an empty PATCH.
        return WriteResult(
            status_changed=False,
            dates_set={},
            dates_skipped=skipped,
            notes_appended=None,
        )

    _patch_row(notion_row_id, properties)

    return WriteResult(
        status_changed=status_changed,
        new_status=new_status,
        dates_set=filtered_dates,
        dates_skipped=skipped,
        notes_appended=notes_append or archive_reason_append,
    )


# ----------------------------------------------------------- Notion body shape


def _build_properties(
    *,
    new_status: Status | None,
    dates_to_write: dict[str, date],
    notes_append: str | None,
    archive_reason_append: str | None,
    current_notes: str | None,
    current_archive_reason: str | None,
) -> dict[str, Any]:
    """Assemble the properties body for a Notion PATCH call.

    An empty dict signals the caller to skip the network call. Notes and
    Archive Reason carry the merged existing-plus-new text, since
    rich_text replaces the field whole.
    """
    properties: dict[str, Any] = {}
    if new_status is not None:
        # The Status column is type select, not status. A status-typed
        # value makes the pages.update PATCH 400, which surfaces as an
        # NtnError swallowed into a node_error. Both readers (graph.py,
        # notion_match.py) already read .get("select"); the writer is
        # the side that must match.
        properties[PROP_STATUS] = {"select": {"name": new_status}}
    for prop_name, day in dates_to_write.items():
        properties[prop_name] = {"date": {"start": day.isoformat()}}
    if notes_append:
        merged = _merge_text(current_notes, notes_append)
        properties[PROP_NOTES] = _rich_text_property(merged)
    if archive_reason_append:
        merged = _merge_text(current_archive_reason, archive_reason_append)
        properties[PROP_ARCHIVE_REASON] = _rich_text_property(merged)
    return properties


def _merge_text(existing: str | None, addition: str) -> str:
    """Concatenate existing and addition with a paragraph break."""
    if not existing:
        return addition
    return f"{existing}\n\n{addition}"


def _rich_text_property(text: str) -> dict[str, Any]:
    """Wrap a string in Notion's rich-text property shape."""
    return {"rich_text": [{"type": "text", "text": {"content": text}}]}


# ------------------------------------------------------------- subprocess


def _patch_row(page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
    """PATCH v1/pages/<id> with properties and return the response payload."""
    body = {"properties": properties}
    args = ["api", f"v1/pages/{page_id}", "-X", "PATCH", "-d", json.dumps(body)]
    return run_ntn(args)


__all__ = [
    "PROP_APPLICATION_DATE",
    "PROP_OA_DATE",
    "PROP_OFFER_DATE",
    "PROP_ONSITE_DATE",
    "PROP_PHONE_SCREEN_DATE",
    "PROP_RECRUITER_SCREEN_DATE",
    "PROP_REJECTION_DATE",
    "PROP_STATUS",
    "REJECTION_EXCERPT_MAX",
    "email_utc_to_et_date",
    "update_application",
]
