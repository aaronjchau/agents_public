"""Pydantic models shared across the news-brief flow.

Link and ParsedEmail are the parser's output and the curator's input: the
parser rewrites each kept anchor to a "[N] TEXT" marker placed immediately
before the anchor text, and the matching Link row resolves N back to a real
URL. StoryCandidate is the curator's output; its source_email_id and
link_id reference a ParsedEmail and a Link within it, resolved downstream
when writing the Notion brief.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from anthropic.types import Usage
from pydantic import BaseModel, Field

Category = Literal[
    "AI & Tech",
    "World & Politics",
    "Business & Economy",
    "Markets",
    "Gadgets & Software",
    "Science & Research",
]


class Link(BaseModel):
    id: int = Field(description="1-indexed within its email")
    url: str = Field(description="final article URL, tracking wrappers stripped")
    anchor_text: str = Field(description="text inside the <a> tag")


class ParsedEmail(BaseModel):
    message_id: str
    sender: str = Field(
        description='slug like "nytimes" / "bloomberg" / "theinformation"',
    )
    subject: str
    received_at: datetime
    plain_text: str = Field(
        description="body text with [1], [2], … inline markers where links were",
    )
    links: list[Link]


class StoryCandidate(BaseModel):
    category: Category
    label: str = Field(description="bold prefix in the rendered brief, e.g. 'OpenAI'")
    summary: str = Field(
        description="1-3 sentences. No em-dashes inside.",
    )
    source_email_id: str = Field(
        description="message_id of the ParsedEmail this story was sourced from",
    )
    link_id: int = Field(description="id of the Link within that email's links list")


@dataclass(frozen=True)
class CurateResult:
    """Output of curator.curate(): stories plus the Anthropic usage record."""

    stories: list[StoryCandidate]
    usage: Usage
    model: str
