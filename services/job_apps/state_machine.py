"""Status state machine for Job Applications rows.

Encodes the monotonic-status invariant: forward through Saved, Applied,
Screen, Interview, Offer, into a terminal state (Rejected, Withdrawn,
Archived) from any non-terminal state, never backward. Lives apart from
notion_writer.py so the graph can consult the same table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.job_apps.types import Status

# Writes are refused entirely on rows in these statuses. The match
# cascade still includes terminal rows as candidates (follow-up mail
# must match them); this set is the writer's guard against mutating one.
TERMINAL_STATUSES: frozenset[Status] = frozenset({"Rejected", "Withdrawn", "Archived"})

# Allowed transitions out of each status. Same-status no-ops are not in
# these sets; is_valid_transition adds them as a separate branch so
# callers can distinguish "no change needed" from a forward edge.
#
# Offer is intentionally not terminal: a row can still move from Offer
# to Rejected (offer declined), Withdrawn, or Archived.
STATUS_TRANSITIONS: dict[Status, frozenset[Status]] = {
    "Saved": frozenset(
        {"Applied", "Screen", "Interview", "Offer", "Rejected", "Withdrawn", "Archived"}
    ),
    "Applied": frozenset({"Screen", "Interview", "Offer", "Rejected", "Withdrawn", "Archived"}),
    "Screen": frozenset({"Interview", "Offer", "Rejected", "Withdrawn", "Archived"}),
    "Interview": frozenset({"Offer", "Rejected", "Withdrawn", "Archived"}),
    "Offer": frozenset({"Rejected", "Withdrawn", "Archived"}),
    "Rejected": frozenset(),
    "Withdrawn": frozenset(),
    "Archived": frozenset(),
}


def is_valid_transition(current: Status, proposed: Status) -> bool:
    """True iff proposed is reachable from current or equal to it.

    Same-status transitions are valid no-ops. Terminal states have empty
    transition sets, and the writer's _guard_terminal rejects terminal
    rows before this function is consulted.
    """
    if proposed == current:
        return True
    return proposed in STATUS_TRANSITIONS[current]


__all__ = [
    "STATUS_TRANSITIONS",
    "TERMINAL_STATUSES",
    "is_valid_transition",
]
