"""Pydantic models and Literal aliases for the Job Apps pipeline.

The contract between the pipeline components, consolidated here to
avoid dependency cycles. Do not reorder, rename, or add Sublabel or
Status values without updating the system prompt: the classifier emits
these strings unchanged and the Notion schema depends on the exact
values.
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# The seven mutually exclusive sublabels; each email gets exactly one.
# Order matches the system prompt so prompt edits and code stay legible
# side by side.
Sublabel = Literal[
    "Offer",
    "Interview Scheduling",
    "Assessment",
    "Recruiter Outreach",
    "Status Update",
    "Application Confirmation",
    "Rejection",
]

# The Job Applications DB Status enum. Saved, Applied, Screen,
# Interview, Offer is monotonic; Rejected, Withdrawn, Archived are
# terminal. The writer's hard-rule guards enforce both invariants.
Status = Literal[
    "Saved",
    "Applied",
    "Screen",
    "Interview",
    "Offer",
    "Rejected",
    "Withdrawn",
    "Archived",
]

# Outcome of the match cascade: matched is a single confident hit,
# ambiguous flags for human review, no_match means nothing plausible.
MatchStatus = Literal["matched", "ambiguous", "no_match"]


class JobAppsClassification(BaseModel):
    """Structured output of the sublabel classifier."""

    sublabel: Sublabel = Field(
        description="The single Job Apps sublabel assigned to this email.",
    )
    reasoning: str = Field(
        description=(
            "1-2 sentence justification for the sublabel call. Used for audit "
            "log + dashboard, not shown to the user."
        ),
    )


class ParsedEmail(BaseModel):
    """Normalized email shape consumed by every downstream node.

    urls comes from the raw text/html part so anchor-only posting links
    survive HTML stripping; empty for plain-text mail, where the match
    cascade falls back to regex over body_text. received_at_utc is the
    Date header in UTC; the writer converts to ET before writing dates.
    """

    sender: str
    subject: str
    body_text: str
    urls: list[str] = Field(default_factory=list)
    received_at_utc: datetime


class MatchResult(BaseModel):
    """Outcome of the match cascade against the Job Applications DB.

    notion_row_id is set only when status is matched; candidates carries
    the rows considered so the flag node can show what was seen.
    """

    status: MatchStatus
    notion_row_id: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class WriteResult(BaseModel):
    """Outcome of a Notion write attempt for a single matched row.

    dates_set maps property name to the date actually written;
    dates_skipped lists fields blocked by an existing value; errored
    marks a guard rejection or failure for the runner to flag.
    """

    status_changed: bool = False
    new_status: str | None = None
    dates_set: dict[str, date] = Field(default_factory=dict)
    dates_skipped: list[str] = Field(default_factory=list)
    notes_appended: str | None = None
    errored: bool = False
    error_msg: str | None = None
