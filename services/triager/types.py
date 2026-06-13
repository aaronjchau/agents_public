"""Pydantic models for the email triager.

Label is the closed set of 12 primary labels; Classification is the
classifier's structured output. Both must stay in sync with the label set
and precedence order in the classifier system prompt.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Label = Literal[
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


class Classification(BaseModel):
    primary_label: Label = Field(
        description="The single primary label assigned to this email.",
    )
    flagged: bool = Field(
        description=(
            "True when the email requires a concrete action (reply needed, "
            "money at risk, deadline, etc.). False for purely informational mail."
        ),
    )
    reasoning: str = Field(
        description=(
            "1-2 sentence justification for the label + flag call. Used for "
            "audit log + dashboard, not shown to the user."
        ),
    )
