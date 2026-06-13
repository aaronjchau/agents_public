"""LangGraph orchestrator for the Job Apps pipeline.

Wires classify, Gmail label, match cascade, and the guarded Notion
writer into one branch-and-merge flow. No replies are drafted; every
reply is reviewed and sent manually. Design notes: docs/design.md.
"""

from __future__ import annotations

import operator
import time
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from functools import lru_cache, wraps
from typing import TYPE_CHECKING, Annotated, Any, cast

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from services.job_apps.classifier import classify_sublabel
from services.job_apps.extractor import EmailMetadata, extract_email_metadata
from services.job_apps.labels import apply_sublabel as _apply_gmail_sublabel
from services.job_apps.notion_match import match_email_to_application
from services.job_apps.notion_writer import update_application
from services.job_apps.types import (
    MatchResult,
    ParsedEmail,
    Sublabel,
    WriteResult,
)
from shared.ntn import run_ntn

if TYPE_CHECKING:
    from collections.abc import Callable

    from anthropic.types import Usage
    from langgraph.graph.state import CompiledStateGraph

# Sublabels router_after_apply forwards to match with no special handling.
# Every sublabel still gets its Gmail label; Rejection and Application
# Confirmation also go to match, Offer flags, Status Update terminates.
_ACTIONABLE_SUBLABELS: frozenset[Sublabel] = frozenset(
    {"Interview Scheduling", "Assessment", "Recruiter Outreach"}
)


def _merge_dicts[V](left: dict[str, V], right: dict[str, V]) -> dict[str, V]:
    """Right-biased shallow merge, the reducer for the dict accumulator channels."""
    return {**left, **right}


class JobAppsState(BaseModel):
    """Graph state carried from node to node.

    Pydantic validation runs only on the initial state; node returns
    merge as plain dicts and the runner re-validates the final dict.
    The accumulator fields (errors, node_timings, token_usage_by_node)
    carry reducers, so nodes contribute deltas. total_cost_usd and
    model_used are set by the runner after invoke() and kept float/str
    so the final re-validation stays cheap.
    """

    message_id: str
    thread_id: str | None = None
    header_message_id: str | None = None
    email_received_at: datetime | None = None
    parsed_email: ParsedEmail | None = None
    sublabel: Sublabel | None = None
    classify_reasoning: str | None = None
    match_result: MatchResult | None = None
    notion_row_id: str | None = None
    email_metadata: EmailMetadata | None = None
    notion_write_result: WriteResult | None = None
    errors: Annotated[list[str], operator.add] = Field(default_factory=list)
    terminal_reason: str | None = None
    node_timings: Annotated[dict[str, int], _merge_dicts] = Field(default_factory=dict)
    token_usage_by_node: Annotated[dict[str, dict[str, int]], _merge_dicts] = Field(
        default_factory=dict
    )
    total_cost_usd: float | None = None
    model_used: str | None = None


def _usage_to_dict(usage: Usage) -> dict[str, int]:
    """Squash an Anthropic Usage into the per-node token-buckets dict.

    Carries only the three buckets the dashboard reads; Job Apps does
    not write to cache, so cache-creation counts stay off the per-node
    breakdown.
    """
    return {
        "input": usage.input_tokens,
        "output": usage.output_tokens,
        "cache_read": usage.cache_read_input_tokens or 0,
    }


# ---------------------------------------------------------------------- nodes


def _node_error(message: str) -> dict[str, Any]:
    """Delta marking a node failure; routers divert to flag on it."""
    return {"errors": [message], "terminal_reason": "node_error"}


def _timed_node[**P](
    name: str, *, catch: bool = True
) -> Callable[[Callable[P, dict[str, Any]]], Callable[P, dict[str, Any]]]:
    """Stamp node_timings[name] onto every update a node returns.

    With catch=True (the default) an exception in the node body becomes
    the standard error delta so failures stay in state instead of
    escaping invoke(). catch=False lets exceptions propagate untimed;
    only parse_email uses it, because the runner needs the original
    exception for HTTP mapping.
    """

    def decorate(fn: Callable[P, dict[str, Any]]) -> Callable[P, dict[str, Any]]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
            started = time.perf_counter()
            try:
                update = fn(*args, **kwargs)
            except Exception as exc:
                if not catch:
                    raise
                update = _node_error(f"{name}: {type(exc).__name__}: {exc}")
            update["node_timings"] = {name: int((time.perf_counter() - started) * 1000)}
            return update

        return wrapper

    return decorate


@_timed_node("parse_email", catch=False)
def parse_email_node(state: JobAppsState, *, service: Any) -> dict[str, Any]:
    """Fetch the Gmail message and extract sender, subject, body, headers, URLs.

    URLs come from the raw text/html part before stripping, since ATS
    posting links often exist only as anchor hrefs that the HTML-to-text
    conversion discards. Plain-text-only emails leave urls empty and the
    match cascade falls back to regex over body_text.

    Unlike the other nodes this one does not catch its own exceptions
    (catch=False): the Gmail messages.get failure shape is the only
    signal the manual endpoint has for HTTP-status mapping, so the
    original HttpError propagates through invoke() to the runner. No
    timing entry is recorded on that path.
    """
    from services.job_apps.notion_match import _extract_urls
    from shared.gmail import (
        extract_plain_text,
        index_headers,
        parse_internal_date,
        walk_for_mime,
    )

    message = (
        service.users().messages().get(userId="me", id=state.message_id, format="full").execute()
    )
    payload = message.get("payload") or {}
    headers = index_headers(payload)
    sender = headers.get("From", "")
    subject = headers.get("Subject", "")
    body_text = extract_plain_text(payload)
    html_body = walk_for_mime(payload, "text/html")
    urls = _extract_urls(html_body) if html_body.strip() else []
    received_at_utc = _parse_received_at_utc(headers)
    email_received_at = parse_internal_date(message.get("internalDate"))

    parsed = ParsedEmail(
        sender=sender,
        subject=subject,
        body_text=body_text,
        urls=urls,
        received_at_utc=received_at_utc,
    )
    return {
        "parsed_email": parsed,
        "thread_id": message.get("threadId"),
        "header_message_id": headers.get("Message-ID") or headers.get("Message-Id"),
        "email_received_at": email_received_at,
    }


def _parse_received_at_utc(headers: dict[str, str]) -> datetime:
    """Best-effort UTC datetime from the email's Date header.

    Falls back to datetime.now(UTC) only when the header is missing or
    unparseable.
    """
    raw = headers.get("Date")
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except (TypeError, ValueError):
            pass
    return datetime.now(tz=UTC)


@_timed_node("classify_sublabel")
def classify_sublabel_node(state: JobAppsState) -> dict[str, Any]:
    """Run the sublabel classifier on the parsed email.

    Token usage and model are recorded only on the happy path; timing
    lands in node_timings on every path.
    """
    if state.parsed_email is None:
        return _node_error("classify_sublabel: parsed_email missing")
    result = classify_sublabel(
        sender=state.parsed_email.sender,
        subject=state.parsed_email.subject,
        body_text=state.parsed_email.body_text,
    )
    return {
        "sublabel": result.classification.sublabel,
        "classify_reasoning": result.classification.reasoning,
        "model_used": result.model,
        "token_usage_by_node": {"classify_sublabel": _usage_to_dict(result.usage)},
    }


@_timed_node("apply_sublabel")
def apply_sublabel_node(state: JobAppsState, *, service: Any) -> dict[str, Any]:
    """Apply the chosen sublabel as a Gmail label on the message.

    Fires for all seven sublabels so the inbox mirrors pipeline state;
    the label CRUD removes the other six so each message carries exactly
    one sublabel.
    """
    if state.sublabel is None:
        return _node_error("apply_sublabel: sublabel missing")
    _apply_gmail_sublabel(message_id=state.message_id, sublabel=state.sublabel, service=service)
    return {}


@_timed_node("match")
def match_node(state: JobAppsState) -> dict[str, Any]:
    """Run the match cascade against the Job Applications DB.

    Token usage is recorded only when the cascade's LLM step fired; URL
    hits and the empty-candidates short-circuit skip Anthropic. Timing
    is always recorded.
    """
    if state.parsed_email is None:
        return _node_error("match: parsed_email missing")
    cascade = match_email_to_application(parsed_email=state.parsed_email)
    result = cascade.result
    update: dict[str, Any] = {"match_result": result}
    if result.status == "matched" and result.notion_row_id is not None:
        update["notion_row_id"] = result.notion_row_id
    if cascade.usage is not None:
        update["token_usage_by_node"] = {"match": _usage_to_dict(cascade.usage)}
        if cascade.model is not None:
            update["model_used"] = cascade.model
    return update


@_timed_node("extract")
def extract_node(state: JobAppsState) -> dict[str, Any]:
    """Extract the structured email data the Notion writer needs.

    The extractor short-circuits for sublabels that need no metadata
    (Status Update, Application Confirmation) by returning an empty
    EmailMetadata, so email_metadata is always non-None on the matched
    path. Token usage is recorded only when the LLM actually ran.
    """
    if state.parsed_email is None or state.sublabel is None:
        return _node_error("extract: parsed_email or sublabel missing")
    result = extract_email_metadata(
        parsed_email=state.parsed_email,
        sublabel=state.sublabel,
    )
    update: dict[str, Any] = {"email_metadata": result.metadata}
    if result.usage is not None:
        update["token_usage_by_node"] = {"extract": _usage_to_dict(result.usage)}
        if result.model is not None:
            update["model_used"] = result.model
    return update


@_timed_node("notion_write")
def notion_write_node(state: JobAppsState) -> dict[str, Any]:
    """Patch the matched Notion row through the hard-rule-guarded writer.

    Pre-fetches the row's current Status, dates, Notes, and Archive
    Reason because the writer never reads; the guards and append-merge
    work off this snapshot. A missing notion_row_id here means the match
    router misrouted (an upstream graph bug) and is rejected as
    node_error.
    """
    if state.sublabel is None:
        return _node_error("notion_write: sublabel missing")
    if state.parsed_email is None:
        return _node_error("notion_write: parsed_email missing")
    if state.notion_row_id is None:
        return _node_error("notion_write: notion_row_id missing despite match")

    from services.job_apps.notion_writer import email_utc_to_et_date
    from services.job_apps.types import Status

    email_received_at_et = email_utc_to_et_date(state.parsed_email.received_at_utc.isoformat())
    current_status_str, current_dates, current_notes, current_archive_reason = _fetch_row_state(
        state.notion_row_id
    )
    # The writer's guards tolerate any string Notion returns (a value
    # outside the closed Status set falls to the terminal-block error),
    # so the cast satisfies mypy without weakening runtime safety.
    current_status = cast("Status", current_status_str)

    # The writer reads current_notes and current_archive_reason to merge
    # appended text so a single PATCH carries the combined content.
    if state.email_metadata is not None:
        metadata: dict[str, Any] = state.email_metadata.to_writer_dict()
    else:
        metadata = {}
    metadata["current_notes"] = current_notes
    metadata["current_archive_reason"] = current_archive_reason

    result = update_application(
        notion_row_id=state.notion_row_id,
        sublabel=state.sublabel,
        email_received_at_et=email_received_at_et,
        email_metadata=metadata,
        current_status=current_status,
        current_dates=current_dates,
    )
    return {"notion_write_result": result}


# Notion date properties read into the writer's current_dates snapshot.
# Mirrors the property constants in notion_writer.py so the row read
# covers exactly the fields _guard_no_overwrite_dates consults.
_DATE_PROPS_TO_READ: tuple[str, ...] = (
    "Application Date",
    "Phone Screen Date",
    "Recruiter Screen Date",
    "Onsite Date",
    "Offer Date",
    "OA Date",
    "Rejection Date",
)


def _fetch_row_state(
    notion_row_id: str,
) -> tuple[str, dict[str, date | None], str | None, str | None]:
    """Fetch a row's current Status, date fields, Notes, and Archive Reason.

    Returns (status, dates, current_notes, current_archive_reason).
    Status defaults to "Saved" when the property is empty; dates maps
    each guarded property to its date or None.

    The Status property on this DB is type select, not status; a
    previous version read the wrong nested key and silently fell back
    to "Saved" for every row.
    """
    payload = run_ntn(["api", f"v1/pages/{notion_row_id}"])
    props = payload.get("properties") or {}

    status_prop = props.get("Status") or {}
    status_obj = status_prop.get("select") or {}
    current_status = status_obj.get("name") or "Saved"

    current_dates: dict[str, date | None] = {}
    for name in _DATE_PROPS_TO_READ:
        prop = props.get(name) or {}
        date_obj = prop.get("date")
        if isinstance(date_obj, dict):
            start = date_obj.get("start")
            if isinstance(start, str):
                try:
                    current_dates[name] = date.fromisoformat(start[:10])
                    continue
                except ValueError:
                    pass
        current_dates[name] = None

    current_notes = _extract_rich_text(props.get("Notes"))
    current_archive_reason = _extract_rich_text(props.get("Archive Reason"))

    return current_status, current_dates, current_notes, current_archive_reason


def _extract_rich_text(prop: Any) -> str | None:
    """Concatenate the plain_text chunks of a Notion rich_text prop."""
    if not isinstance(prop, dict):
        return None
    blocks = prop.get("rich_text")
    if not isinstance(blocks, list):
        return None
    chunks: list[str] = []
    for block in blocks:
        if isinstance(block, dict):
            text = block.get("plain_text")
            if isinstance(text, str):
                chunks.append(text)
    joined = "".join(chunks).strip()
    return joined or None


@_timed_node("flag")
def flag_node(state: JobAppsState) -> dict[str, Any]:
    """Record why the run ended at human review.

    Keeps any reason already set; otherwise derives one from the match
    result or the offer branch. The audit row's terminal_reason column
    is the source of truth the dashboard reads.
    """
    if state.terminal_reason is not None:
        return {}
    if state.match_result is not None:
        reason = f"flagged_{state.match_result.status}"
    elif state.sublabel == "Offer":
        reason = "flagged_offer"
    else:
        reason = "flagged_for_human"
    return {"terminal_reason": reason}


# -------------------------------------------------------------------- routers


def router_after_classify(state: JobAppsState) -> str:
    """Route every successful classification through apply_sublabel.

    All seven sublabels get their Gmail label first; per-sublabel
    routing happens in router_after_apply. A node error or a missing
    sublabel diverts to flag so the audit row records the failure
    without raising past the graph boundary.
    """
    if state.terminal_reason == "node_error":
        return "error"
    if state.sublabel is None:
        return "error"
    return "apply"


def router_after_apply(state: JobAppsState) -> str:
    """Branch on the sublabel after its Gmail label has been applied.

    Rejection, Application Confirmation, and the actionable trio go to
    match, which must precede every Notion write. Offer flags for a
    human (the system never acts on an offer); Status Update is
    informational and ends the run. An apply_sublabel failure routes to
    flag so the Gmail error is recorded.
    """
    if state.terminal_reason == "node_error":
        return "error"
    if state.sublabel == "Rejection":
        return "rejection"
    if state.sublabel == "Offer":
        return "offer"
    if state.sublabel == "Status Update":
        return "status_update"
    if state.sublabel == "Application Confirmation":
        return "application_confirmation"
    if state.sublabel in _ACTIONABLE_SUBLABELS:
        return "actionable"
    return "error"


def router_after_match(state: JobAppsState) -> str:
    """Branch on the match cascade outcome.

    Returns matched, ambiguous, no_match, or error; flag_node fills in
    the terminal reason for the non-matched branches.
    """
    if state.terminal_reason == "node_error":
        return "error"
    if state.match_result is None:
        return "error"
    return state.match_result.status


# --------------------------------------------------------------- graph builder


def _build_graph(*, service_factory: Any) -> Any:
    """Assemble the StateGraph with the injected service factory.

    service_factory is a zero-arg callable returning a googleapiclient
    Resource; the closure lets every node reuse one Gmail client without
    threading it through state.
    """
    builder = StateGraph(JobAppsState)

    service = service_factory()

    builder.add_node("parse_email", lambda s: parse_email_node(s, service=service))
    builder.add_node("classify_sublabel", classify_sublabel_node)
    builder.add_node("apply_sublabel", lambda s: apply_sublabel_node(s, service=service))
    builder.add_node("match", match_node)
    builder.add_node("extract", extract_node)
    builder.add_node("notion_write", notion_write_node)
    builder.add_node("flag", flag_node)

    builder.add_edge(START, "parse_email")
    builder.add_edge("parse_email", "classify_sublabel")

    # All seven sublabels get their Gmail label before per-sublabel
    # routing so the inbox mirrors pipeline state; a classify error
    # short-circuits to flag.
    builder.add_conditional_edges(
        "classify_sublabel",
        router_after_classify,
        {
            "apply": "apply_sublabel",
            "error": "flag",
        },
    )

    # Match must precede every Notion write (notion_write_node needs
    # notion_row_id); Offer flags for a human and Status Update ends.
    builder.add_conditional_edges(
        "apply_sublabel",
        router_after_apply,
        {
            "rejection": "match",
            "application_confirmation": "match",
            "actionable": "match",
            "offer": "flag",
            "status_update": END,
            "error": "flag",
        },
    )

    builder.add_conditional_edges(
        "match",
        router_after_match,
        {
            "matched": "extract",
            "ambiguous": "flag",
            "no_match": "flag",
            "error": "flag",
        },
    )

    builder.add_edge("extract", "notion_write")
    builder.add_edge("notion_write", END)
    builder.add_edge("flag", END)

    return builder


def compile_job_apps_graph(
    *, service_factory: Any | None = None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build and compile the Job Apps graph.

    service_factory defaults to the production build_gmail_service;
    tests pass a lambda returning a mock to bypass OAuth.
    """
    if service_factory is None:
        from shared.gmail import build_gmail_service

        service_factory = build_gmail_service
    builder = _build_graph(service_factory=service_factory)
    return cast("CompiledStateGraph[Any, Any, Any, Any]", builder.compile())


@lru_cache(maxsize=1)
def cached_compiled_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the production graph once per process and reuse it."""
    return compile_job_apps_graph()


__all__ = [
    "JobAppsState",
    "apply_sublabel_node",
    "cached_compiled_graph",
    "classify_sublabel_node",
    "compile_job_apps_graph",
    "extract_node",
    "flag_node",
    "match_node",
    "notion_write_node",
    "parse_email_node",
    "router_after_apply",
    "router_after_classify",
    "router_after_match",
]
