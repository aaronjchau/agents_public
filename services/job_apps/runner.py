"""Single-message Job Apps pipeline runner.

process_job_apps_message is the unit of work behind the manual endpoint
and the Triager dispatch hook: idempotency gate, graph invoke, audit
row on every outcome. Design notes: docs/design.md.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from functools import partial
from typing import TYPE_CHECKING, Any

from anyio import to_thread
from sqlalchemy import select

from services.job_apps.graph import JobAppsState, cached_compiled_graph, compile_job_apps_graph
from shared.anthropic_cost import compute_cost_usd
from shared.db import JobAppsRun, get_session

if TYPE_CHECKING:
    from anthropic.types import Usage
    from googleapiclient.discovery import Resource
    from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)


def _aggregate_token_buckets(
    token_usage_by_node: dict[str, dict[str, int]],
) -> dict[str, int]:
    """Sum per-node token buckets into single totals for the audit row.

    The audit row records one total per bucket so the dashboard's cost
    and volume charts need no join.
    """
    totals = {"input": 0, "output": 0, "cache_read": 0}
    for buckets in token_usage_by_node.values():
        totals["input"] += buckets.get("input", 0)
        totals["output"] += buckets.get("output", 0)
        totals["cache_read"] += buckets.get("cache_read", 0)
    return totals


def _compute_total_cost_usd(
    model: str | None, token_usage_by_node: dict[str, dict[str, int]]
) -> Decimal | None:
    """Roll the aggregated token buckets into a single Decimal cost.

    Builds a synthetic Usage so the canonical compute_cost_usd price
    table is reused; all Job Apps calls share one model, so a single
    price lookup covers them. Returns None when no LLM calls fired.
    """
    if model is None or not token_usage_by_node:
        return None
    totals = _aggregate_token_buckets(token_usage_by_node)
    if totals["input"] == 0 and totals["output"] == 0 and totals["cache_read"] == 0:
        return None
    # Defer the import to keep the runner cheap to import.
    from anthropic.types import Usage as _Usage

    synthetic_usage: Usage = _Usage(
        input_tokens=totals["input"],
        output_tokens=totals["output"],
        cache_read_input_tokens=totals["cache_read"],
    )
    return compute_cost_usd(model, synthetic_usage)


async def process_job_apps_message(
    message_id: str,
    *,
    service: Resource | None = None,
    raise_on_failure: bool = False,
) -> JobAppsState:
    """Run the Job Apps pipeline for a single Gmail message.

    Idempotent on success: a non-errored job_apps_runs row short-circuits
    the graph, and the Notion and Gmail mutations are not re-issued (the
    PATCH converges; re-running is not worth the cost). Errored rows do
    not short-circuit, so failed messages replay. The gate is
    check-then-act and only covers serial deliveries; near-simultaneous
    dispatches can double-run the graph, with audit rows converging by
    primary key.

    Two callers, two error contracts: the Triager dispatch hook uses
    the default raise_on_failure=False, so failures never propagate past
    this function and the parent webhook stays 200, while the manual
    endpoint passes True to map exceptions to HTTP statuses. The audit
    row is written before any re-raise.
    """
    existing = await _load_existing(message_id)
    if existing is not None:
        logger.info(
            "job_apps.skipped_already_processed",
            extra={
                "message_id": message_id,
                "sublabel": existing.sublabel,
                "match_status": existing.match_status,
            },
        )
        return _reconstruct_state(message_id, existing)

    started = time.perf_counter()
    state = JobAppsState(message_id=message_id)
    final_state = state
    errored = False
    error_msg: str | None = None
    raised_exc: Exception | None = None

    try:
        graph = _build_graph_for_run(service=service)
        # Metadata makes LangSmith traces filterable by message and
        # source; LangGraph already emits the traces natively.
        config: RunnableConfig = {
            "metadata": {"message_id": message_id, "agent": "job_apps"},
            "tags": ["job_apps"],
        }
        # graph.invoke is fully synchronous (LLM calls, Gmail HTTP, ntn
        # subprocesses) and can block for tens of seconds; run it on a
        # worker thread so this container keeps answering /health and
        # concurrent requests.
        result_dict: dict[str, Any] = await to_thread.run_sync(
            partial(graph.invoke, state, config=config)
        )
        # Set the cost fields on the merged dict before re-validating so
        # the returned JobAppsState preserves them.
        model_used = result_dict.get("model_used")
        token_usage_by_node = result_dict.get("token_usage_by_node") or {}
        total_cost = _compute_total_cost_usd(model_used, token_usage_by_node)
        if total_cost is not None:
            result_dict["total_cost_usd"] = float(total_cost)
        final_state = JobAppsState.model_validate(result_dict)
    except Exception as exc:
        errored = True
        error_msg = f"{type(exc).__name__}: {exc}"
        raised_exc = exc
        logger.exception("job_apps.graph_invoke_failed", extra={"message_id": message_id})
        final_state = state.model_copy(
            update={
                "errors": [*state.errors, error_msg],
                "terminal_reason": "node_error",
            }
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    run_errored = errored or final_state.terminal_reason == "node_error"
    await _write_audit_row(
        message_id=message_id,
        state=final_state,
        errored=run_errored,
        error_msg=error_msg or _join_errors(final_state.errors),
        latency_ms=latency_ms,
    )

    # Surface failures on the dispatch path. Without this, a node_error
    # is swallowed (the runner never raises for the Triager) and the only
    # trace is an audit row nobody watches, exactly how the Notion-write
    # outage stayed silent. WARNING keeps it grep-able in Modal logs.
    if run_errored:
        logger.warning(
            "job_apps.run_errored",
            extra={
                "message_id": message_id,
                "sublabel": final_state.sublabel,
                "terminal_reason": final_state.terminal_reason,
                "error_msg": error_msg or _join_errors(final_state.errors),
            },
        )

    if raised_exc is not None and raise_on_failure:
        raise raised_exc
    return final_state


def _build_graph_for_run(*, service: Resource | None) -> Any:
    """Pick the cached graph when no service is injected, a fresh one otherwise.

    Tests and the Triager dispatch pass a service so the graph reuses an
    already-authenticated client; the cached graph bakes in
    build_gmail_service, the right default for the manual endpoint.
    """
    if service is None:
        return cached_compiled_graph()
    return compile_job_apps_graph(service_factory=lambda: service)


async def _load_existing(message_id: str) -> JobAppsRun | None:
    """Return a prior successful run for message_id, else None.

    Errored rows are excluded so the idempotency gate never blocks a
    previously failed message from replaying once the underlying bug is
    fixed.
    """
    async with get_session() as session:
        result = await session.execute(
            select(JobAppsRun).where(
                JobAppsRun.message_id == message_id,
                JobAppsRun.errored.is_(False),
            )
        )
        return result.scalar_one_or_none()


def _reconstruct_state(message_id: str, row: JobAppsRun) -> JobAppsState:
    """Build a synthetic JobAppsState from an existing audit row.

    Not a perfect replay (parsed_email and match candidates are not
    stored), but it covers the fields the dispatch caller reads.
    """
    from services.job_apps.types import MatchResult, Sublabel

    sublabel: Sublabel | None = row.sublabel  # type: ignore[assignment]
    match_result: MatchResult | None = None
    if row.match_status:
        match_result = MatchResult(
            status=row.match_status,  # type: ignore[arg-type]
            notion_row_id=row.notion_row_id,
        )
    return JobAppsState(
        message_id=message_id,
        sublabel=sublabel,
        match_result=match_result,
        notion_row_id=row.notion_row_id,
        terminal_reason=row.terminal_reason,
        errors=[row.error_msg] if row.error_msg else [],
    )


async def _write_audit_row(
    *,
    message_id: str,
    state: JobAppsState,
    errored: bool,
    error_msg: str | None,
    latency_ms: int,
) -> None:
    """Upsert a single job_apps_runs row by primary key.

    session.merge (not add) lets a replay of a previously errored
    message update its row in place instead of colliding on the
    message_id key, preserving server-managed columns like
    classified_at. The write is wrapped in try/except because an audit
    failure must not break the Triager dispatch contract; it is logged
    and swallowed. Observability columns stay null when the graph
    errored before any LLM ran.
    """
    status_changed = False
    new_status: str | None = None
    if state.notion_write_result is not None:
        status_changed = state.notion_write_result.status_changed
        new_status = state.notion_write_result.new_status

    totals = _aggregate_token_buckets(state.token_usage_by_node)
    has_token_data = bool(state.token_usage_by_node)
    cost_decimal: Decimal | None = (
        Decimal(str(state.total_cost_usd)) if state.total_cost_usd is not None else None
    )

    try:
        async with get_session() as session:
            await session.merge(
                JobAppsRun(
                    message_id=message_id,
                    email_received_at=state.email_received_at,
                    sublabel=state.sublabel,
                    match_status=state.match_result.status if state.match_result else None,
                    notion_row_id=state.notion_row_id,
                    status_changed=status_changed,
                    new_status=new_status,
                    terminal_reason=state.terminal_reason,
                    errored=errored,
                    error_msg=error_msg,
                    latency_ms=latency_ms,
                    model=state.model_used,
                    input_tokens=totals["input"] if has_token_data else None,
                    output_tokens=totals["output"] if has_token_data else None,
                    cache_read_tokens=totals["cache_read"] if has_token_data else None,
                    cost_usd=cost_decimal,
                    node_timings_ms=state.node_timings or None,
                )
            )
            await session.commit()
    except Exception:
        logger.exception("job_apps.audit_row_write_failed", extra={"message_id": message_id})


def _join_errors(errors: list[str]) -> str | None:
    if not errors:
        return None
    return " | ".join(errors)


__all__ = ["process_job_apps_message"]
