"""Pydantic response models for the /job-apps/drafts endpoint.

The only dashboard view served by this API; the rest read Postgres
directly.
"""

from datetime import datetime

from pydantic import BaseModel


class JobAppsDraftRow(BaseModel):
    """One pending Gmail draft, joined to its audit row when present.

    message_id, sublabel, and created_at are None when the draft has no
    audit row yet (processing race window, or a manually written draft).
    """

    draft_id: str
    message_id: str | None
    subject: str | None
    sender: str | None
    sublabel: str | None
    created_at: datetime | None
    gmail_deep_link: str


class JobAppsDraftsResponse(BaseModel):
    drafts: list[JobAppsDraftRow]
