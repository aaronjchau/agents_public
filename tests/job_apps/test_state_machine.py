"""Pure unit tests for the status state machine.

The transitions table is small enough that we cover every cell of the
8-by-8 matrix exhaustively rather than relying on representative cases.
"""

from __future__ import annotations

from typing import get_args

import pytest

from services.job_apps.state_machine import (
    STATUS_TRANSITIONS,
    TERMINAL_STATUSES,
    is_valid_transition,
)
from services.job_apps.types import Status


@pytest.fixture(scope="module")
def all_statuses() -> tuple[Status, ...]:
    return get_args(Status)


# ---------------------------------------------------------------------- table


def test_terminal_statuses_set_is_exactly_the_three_terminals() -> None:
    assert frozenset({"Rejected", "Withdrawn", "Archived"}) == TERMINAL_STATUSES


def test_table_has_an_entry_for_every_status(all_statuses: tuple[Status, ...]) -> None:
    assert set(STATUS_TRANSITIONS) == set(all_statuses)


def test_terminal_statuses_have_empty_transition_sets() -> None:
    for terminal in TERMINAL_STATUSES:
        assert STATUS_TRANSITIONS[terminal] == frozenset()


def test_saved_can_transition_to_every_other_status() -> None:
    expected = {"Applied", "Screen", "Interview", "Offer", "Rejected", "Withdrawn", "Archived"}
    assert STATUS_TRANSITIONS["Saved"] == frozenset(expected)


def test_applied_cannot_regress_to_saved() -> None:
    assert "Saved" not in STATUS_TRANSITIONS["Applied"]


def test_screen_cannot_regress() -> None:
    assert STATUS_TRANSITIONS["Screen"].isdisjoint({"Saved", "Applied"})


def test_interview_cannot_regress() -> None:
    assert STATUS_TRANSITIONS["Interview"].isdisjoint({"Saved", "Applied", "Screen"})


def test_offer_can_only_advance_to_terminal() -> None:
    """Offer is not terminal, but it only flows to the three terminal statuses."""
    assert STATUS_TRANSITIONS["Offer"] == TERMINAL_STATUSES


# ---------------------------------------------------------------------- function


def test_no_op_transition_is_always_valid(all_statuses: tuple[Status, ...]) -> None:
    """current == proposed is a valid no-op for every status, even terminals."""
    for status in all_statuses:
        assert is_valid_transition(status, status) is True


def test_every_forward_edge_is_valid(all_statuses: tuple[Status, ...]) -> None:
    for src in all_statuses:
        for dst in STATUS_TRANSITIONS[src]:
            assert is_valid_transition(src, dst) is True, f"{src} → {dst} should be valid"


def test_every_non_edge_is_invalid(all_statuses: tuple[Status, ...]) -> None:
    """Every (src, dst) pair outside the transition table is invalid."""
    for src in all_statuses:
        for dst in all_statuses:
            if dst == src:
                continue
            if dst in STATUS_TRANSITIONS[src]:
                continue
            assert is_valid_transition(src, dst) is False, f"{src} → {dst} must be invalid"


@pytest.mark.parametrize(
    ("current", "proposed", "expected"),
    [
        ("Saved", "Applied", True),
        ("Saved", "Screen", True),
        ("Saved", "Offer", True),
        ("Saved", "Rejected", True),
        ("Applied", "Saved", False),
        ("Screen", "Saved", False),
        ("Screen", "Applied", False),
        ("Interview", "Screen", False),
        ("Offer", "Interview", False),
        ("Rejected", "Saved", False),
        ("Rejected", "Applied", False),
        ("Rejected", "Rejected", True),
        ("Withdrawn", "Applied", False),
        ("Archived", "Saved", False),
    ],
)
def test_representative_transitions(current: Status, proposed: Status, expected: bool) -> None:
    assert is_valid_transition(current, proposed) is expected
