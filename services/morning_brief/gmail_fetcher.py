"""Fetch labeled Gmail for the Morning Brief's Emails section.

Searches each category label over the last 24h, deduping across labels by
message id: EMAIL_CATEGORIES is iterated in priority order and the first
hit wins. The News label is not searched; news comes from the News DB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.morning_brief import constants
from services.morning_brief.types import EmailItem
from shared.gmail import build_gmail_service, index_headers

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

DEFAULT_MAX_PER_CATEGORY = 30


def fetch_emails(
    *,
    service: Resource | None = None,
    max_per_category: int = DEFAULT_MAX_PER_CATEGORY,
) -> list[EmailItem]:
    """Return deduped emails from the last 24h, grouped by category priority."""
    api = service or build_gmail_service()
    seen: set[str] = set()
    out: list[EmailItem] = []
    for category in constants.EMAIL_CATEGORIES:
        query = f"{category.query} newer_than:1d"
        response = (
            api.users().messages().list(userId="me", q=query, maxResults=max_per_category).execute()
        )
        for stub in response.get("messages") or []:
            message_id = stub.get("id")
            if not isinstance(message_id, str) or message_id in seen:
                continue
            seen.add(message_id)
            message = (
                api.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject"],
                )
                .execute()
            )
            headers = index_headers(message.get("payload") or {})
            out.append(
                EmailItem(
                    message_id=message_id,
                    sender=_sender_display(headers.get("From", "")),
                    subject=headers.get("Subject", ""),
                    category_priority=category.priority,
                )
            )
    return out


def _sender_display(from_header: str) -> str:
    """Extract a human display name from a From header.

    "Amazon <ship@amazon.com>" yields "Amazon"; a bare address passes
    through.
    """
    header = from_header.strip()
    if "<" in header:
        name = header.split("<", 1)[0].strip().strip('"').strip()
        if name:
            return name
        return header.split("<", 1)[1].rstrip(">").strip()
    return header
