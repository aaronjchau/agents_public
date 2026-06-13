"""Tests for services.job_apps.runner.process_job_apps_message.

Covers idempotency, error isolation, and the audit-row shape on success
and failure; the DB session is mocked.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.job_apps.graph import JobAppsState
from services.job_apps.runner import process_job_apps_message
from services.job_apps.types import MatchResult, WriteResult

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator


# --------------------------------------------------------------- DB fixtures


def _scalar_result(scalar_one_or_none: Any = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=scalar_one_or_none)
    return result


def _make_session(execute_returns: list[Any]) -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    # merge is a coroutine on AsyncSession, so it must be an AsyncMock;
    # awaiting a plain MagicMock raises TypeError, which the runner's
    # broad except would swallow, silently no-op'ing the audit write and
    # breaking every assertion below.
    session.merge = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_returns)
    return session


def _patch_get_session(sessions: list[MagicMock]) -> Any:
    """Patch the runner's get_session to yield sessions in order."""
    sessions_iter: Iterator[MagicMock] = iter(sessions)

    @asynccontextmanager
    async def _get_session() -> AsyncGenerator[MagicMock, None]:
        yield next(sessions_iter)

    return patch("services.job_apps.runner.get_session", new=_get_session)


def _existing_run_row(
    *,
    message_id: str = "m-1",
    sublabel: str = "Interview Scheduling",
    match_status: str | None = "matched",
    notion_row_id: str | None = "row-7",
    terminal_reason: str | None = None,
    error_msg: str | None = None,
) -> MagicMock:
    row = MagicMock()
    row.message_id = message_id
    row.sublabel = sublabel
    row.match_status = match_status
    row.notion_row_id = notion_row_id
    row.terminal_reason = terminal_reason
    row.error_msg = error_msg
    return row


# ---------------------------------------------------------------- happy path


async def test_runner_invokes_graph_and_writes_audit_row_on_success() -> None:
    """With no existing row the graph runs and an audit row is written."""
    final_state = JobAppsState(
        message_id="m-1",
        sublabel="Interview Scheduling",
        match_result=MatchResult(status="matched", notion_row_id="row-7"),
        notion_row_id="row-7",
        notion_write_result=WriteResult(status_changed=True, new_status="Screen"),
    )
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state.model_dump()

    # Two sessions: one for the existing-row check (returns None), one
    # for the audit-row insert.
    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        result = await process_job_apps_message("m-1", service=MagicMock())

    assert result.message_id == "m-1"
    assert result.sublabel == "Interview Scheduling"
    fake_graph.invoke.assert_called_once()

    # Audit row was added with the right fields.
    audit_session.merge.assert_awaited_once()
    audit_row = audit_session.merge.call_args[0][0]
    assert audit_row.message_id == "m-1"
    assert audit_row.sublabel == "Interview Scheduling"
    assert audit_row.match_status == "matched"
    assert audit_row.notion_row_id == "row-7"
    assert audit_row.status_changed is True
    assert audit_row.new_status == "Screen"
    assert audit_row.errored is False
    audit_session.commit.assert_awaited_once()


# ---------------------------------------------------------------- idempotency


async def test_runner_short_circuits_when_audit_row_exists() -> None:
    """An existing successful row skips the graph and reconstructs state."""
    existing = _existing_run_row(
        message_id="m-2",
        sublabel="Recruiter Outreach",
        match_status="matched",
        notion_row_id="row-x",
    )
    lookup_session = _make_session([_scalar_result(scalar_one_or_none=existing)])
    fake_graph = MagicMock()

    with (
        _patch_get_session([lookup_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        result = await process_job_apps_message("m-2")

    fake_graph.invoke.assert_not_called()
    assert result.message_id == "m-2"
    assert result.sublabel == "Recruiter Outreach"
    assert result.match_result is not None
    assert result.match_result.status == "matched"
    assert result.notion_row_id == "row-x"


async def test_runner_idempotent_path_handles_terminal_reason() -> None:
    """An audit row with terminal_reason='flagged_offer' replays into state."""
    existing = _existing_run_row(
        message_id="m-3",
        sublabel="Offer",
        match_status=None,
        notion_row_id=None,
        terminal_reason="flagged_offer",
    )
    lookup_session = _make_session([_scalar_result(scalar_one_or_none=existing)])

    with _patch_get_session([lookup_session]):
        result = await process_job_apps_message("m-3")

    assert result.sublabel == "Offer"
    assert result.terminal_reason == "flagged_offer"
    assert result.match_result is None


async def test_runner_replays_errored_row_does_not_short_circuit() -> None:
    """A previously errored run replays instead of short-circuiting.

    _load_existing filters errored rows out, so the gate never blocks a
    failed message from healing once the underlying bug is fixed.
    """
    final_state = JobAppsState(
        message_id="m-replay",
        sublabel="Rejection",
        match_result=MatchResult(status="matched", notion_row_id="row-9"),
        notion_row_id="row-9",
        notion_write_result=WriteResult(status_changed=True, new_status="Rejected"),
    )
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state.model_dump()

    # The errored row is filtered out at the DB layer, so the lookup yields None.
    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        result = await process_job_apps_message("m-replay", service=MagicMock())

    # Re-ran the graph instead of short-circuiting on the errored row.
    fake_graph.invoke.assert_called_once()
    assert result.sublabel == "Rejection"
    assert result.notion_write_result is not None
    assert result.notion_write_result.new_status == "Rejected"
    # The idempotency lookup query carries the errored filter.
    lookup_stmt = str(lookup_session.execute.call_args[0][0])
    assert "errored" in lookup_stmt.lower()


# ------------------------------------------------------------ error isolation


async def test_runner_captures_graph_invoke_exception_does_not_raise() -> None:
    """A bare invoke() raise is captured in state and the runner returns cleanly."""
    fake_graph = MagicMock()
    fake_graph.invoke.side_effect = RuntimeError("langgraph blew up")

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        result = await process_job_apps_message("m-err")

    assert result.terminal_reason == "node_error"
    assert any("langgraph blew up" in e for e in result.errors)
    # Audit row records the failure even when invoke() raised.
    audit_session.merge.assert_awaited_once()
    audit_row = audit_session.merge.call_args[0][0]
    assert audit_row.errored is True
    assert audit_row.error_msg is not None
    assert "langgraph blew up" in audit_row.error_msg


async def test_runner_re_raises_when_raise_on_failure_true() -> None:
    """raise_on_failure=True re-raises the original exception after the audit row."""
    fake_graph = MagicMock()
    original = RuntimeError("kaboom")
    fake_graph.invoke.side_effect = original

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
        pytest.raises(RuntimeError, match="kaboom") as exc_info,
    ):
        await process_job_apps_message("m-raise", raise_on_failure=True)

    # Exception identity preserved (HttpError 404 detection downstream).
    assert exc_info.value is original
    # Audit row was still written before the re-raise.
    audit_session.merge.assert_awaited_once()
    audit_row = audit_session.merge.call_args[0][0]
    assert audit_row.errored is True
    assert "kaboom" in audit_row.error_msg


async def test_runner_does_not_raise_on_success_with_raise_on_failure_true() -> None:
    """raise_on_failure=True only matters in the error path."""
    final_state = JobAppsState(message_id="m-ok", sublabel="Status Update")
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state.model_dump()

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        result = await process_job_apps_message("m-ok", raise_on_failure=True)

    assert result.message_id == "m-ok"


async def test_runner_swallows_audit_row_write_failure() -> None:
    """An audit-write DB failure is logged, never propagated, per the dispatch contract."""
    final_state = JobAppsState(message_id="m-4", sublabel="Interview Scheduling")
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state.model_dump()

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])
    audit_session.commit.side_effect = RuntimeError("db gone")

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        # Should not raise even though commit() raises.
        result = await process_job_apps_message("m-4")

    assert result.message_id == "m-4"
    assert result.sublabel == "Interview Scheduling"


async def test_runner_records_terminal_reason_node_error_when_state_signals_it() -> None:
    """A final state with terminal_reason node_error marks the audit row errored."""
    final_state = JobAppsState(
        message_id="m-5",
        sublabel="Interview Scheduling",
        terminal_reason="node_error",
        errors=["match: ntn timeout"],
    )
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state.model_dump()

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        result = await process_job_apps_message("m-5")

    assert result.terminal_reason == "node_error"
    audit_row = audit_session.merge.call_args[0][0]
    assert audit_row.errored is True
    assert audit_row.error_msg == "match: ntn timeout"
    assert audit_row.terminal_reason == "node_error"


# ---------------------------------------------------------- service injection


async def test_runner_passes_service_through_to_graph_factory() -> None:
    """An injected service is threaded through to the per-run graph build."""
    final_state = JobAppsState(message_id="m-6", sublabel="Status Update")
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state.model_dump()
    injected_service = MagicMock(name="injected_service")

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph) as builder,
    ):
        await process_job_apps_message("m-6", service=injected_service)

    builder.assert_called_once_with(service=injected_service)


async def test_runner_uses_cached_graph_when_no_service_injected() -> None:
    """Without an injected service the cached compiled graph is used."""
    final_state = JobAppsState(message_id="m-7", sublabel="Status Update")
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state.model_dump()

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner.cached_compiled_graph", return_value=fake_graph),
    ):
        await process_job_apps_message("m-7")

    fake_graph.invoke.assert_called_once()


async def test_runner_returns_jobappsstate_instance_not_dict() -> None:
    """Runner converts the dict-shaped graph output back into a Pydantic model."""
    final_state = JobAppsState(
        message_id="m-8",
        sublabel="Interview Scheduling",
    )
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state.model_dump()

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        result = await process_job_apps_message("m-8")

    assert isinstance(result, JobAppsState)


# ----------------------------------------------------------- observability


async def test_runner_aggregates_tokens_and_computes_cost() -> None:
    """Per-node token usage sums onto the audit row and one cost is computed."""
    final_state_dict = JobAppsState(
        message_id="m-cost",
        sublabel="Interview Scheduling",
        model_used="claude-opus-4-7",
        token_usage_by_node={
            "classify_sublabel": {"input": 1000, "output": 100, "cache_read": 0},
            "match": {"input": 2000, "output": 200, "cache_read": 50},
        },
    ).model_dump()
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state_dict

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        result = await process_job_apps_message("m-cost")

    audit_row = audit_session.merge.call_args[0][0]
    assert audit_row.model == "claude-opus-4-7"
    assert audit_row.input_tokens == 3000  # 1000 + 2000
    assert audit_row.output_tokens == 300  # 100 + 200
    assert audit_row.cache_read_tokens == 50  # 0 + 50
    # Opus 4.7 pricing: $5/M input, $25/M output, $0.50/M cache read.
    # 3000 * 5 + 300 * 25 + 50 * 0.5 = 15000 + 7500 + 25 = 22525 / 1e6 = $0.022525
    from decimal import Decimal as _D

    assert audit_row.cost_usd == _D("0.022525")
    assert result.total_cost_usd is not None
    # Floating point comparison since the runner re-validates as float.
    assert abs(result.total_cost_usd - 0.022525) < 1e-9


async def test_runner_persists_node_timings_to_audit_row() -> None:
    """node_timings on state lands on the audit row as a JSONB dict."""
    timings = {"parse_email": 12, "classify_sublabel": 1840, "match": 2230}
    final_state_dict = JobAppsState(
        message_id="m-timings",
        sublabel="Status Update",
        node_timings=timings,
    ).model_dump()
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state_dict

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        await process_job_apps_message("m-timings")

    audit_row = audit_session.merge.call_args[0][0]
    assert audit_row.node_timings_ms == timings


async def test_runner_no_token_data_persists_nulls_not_zeros() -> None:
    """An early-error run leaves token columns NULL, not zero.

    Zeros would show a misleading $0 row on the dashboard for a failed
    run; NULL keeps the column truthful.
    """
    final_state_dict = JobAppsState(
        message_id="m-nolm",
        terminal_reason="node_error",
        errors=["parse_email blew up"],
    ).model_dump()
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state_dict

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        await process_job_apps_message("m-nolm")

    audit_row = audit_session.merge.call_args[0][0]
    assert audit_row.model is None
    assert audit_row.input_tokens is None
    assert audit_row.output_tokens is None
    assert audit_row.cache_read_tokens is None
    assert audit_row.cost_usd is None
    assert audit_row.node_timings_ms is None


async def test_runner_passes_runnable_config_with_metadata_to_graph() -> None:
    """RunnableConfig carries message_id metadata and the job_apps tag for LangSmith."""
    final_state = JobAppsState(message_id="m-cfg", sublabel="Status Update")
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state.model_dump()

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        await process_job_apps_message("m-cfg")

    fake_graph.invoke.assert_called_once()
    # invoke(state, config=...); the first positional is the state.
    call = fake_graph.invoke.call_args
    config = call.kwargs.get("config") or (call.args[1] if len(call.args) > 1 else None)
    assert config is not None
    assert config["metadata"]["message_id"] == "m-cfg"
    assert config["metadata"]["agent"] == "job_apps"
    assert "job_apps" in config["tags"]


async def test_runner_writes_audit_row_with_latency() -> None:
    """Every run records latency on the audit row."""
    final_state = JobAppsState(
        message_id="m-events",
        sublabel="Status Update",
        terminal_reason=None,
    )
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = final_state.model_dump()

    lookup_session = _make_session([_scalar_result(scalar_one_or_none=None)])
    audit_session = _make_session([])

    with (
        _patch_get_session([lookup_session, audit_session]),
        patch("services.job_apps.runner._build_graph_for_run", return_value=fake_graph),
    ):
        await process_job_apps_message("m-events")

    # The audit session should have had a JobAppsRun merged + committed.
    audit_session.merge.assert_awaited_once()
    audit_session.commit.assert_awaited_once()
    added_row = audit_session.merge.call_args.args[0]
    assert added_row.message_id == "m-events"
    assert added_row.sublabel == "Status Update"
    assert added_row.latency_ms is not None and added_row.latency_ms >= 0
