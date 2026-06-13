"""Pull labeled News emails directly from the Gmail API.

Authenticates via the OAuth refresh-token grant from settings; google-auth
mints short-lived access tokens automatically, so there is no keyring,
on-disk credential, or subprocess. messages.list finds matching ids and
messages.get fetches each full body, yielding FetchedEmail records ready
for parse_email_html().
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from shared.gmail import (
    build_gmail_service,
    index_headers,
    parse_message_received_at,
    walk_for_mime,
)

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

DEFAULT_LABEL = "News"
DEFAULT_MAX = 200


class FetchedEmail(BaseModel):
    """Raw email + metadata as returned by Gmail, before HTML parsing."""

    message_id: str
    sender_slug: str
    subject: str
    received_at: datetime
    body_html: str


def fetch_news_emails(
    *,
    since: datetime,
    until: datetime | None = None,
    label: str = DEFAULT_LABEL,
    max_results: int = DEFAULT_MAX,
    service: Resource | None = None,
) -> list[FetchedEmail]:
    """Fetch labeled mail in [since, until); return an empty list if none match.

    until=None means now (UTC). max_results is the per-call page size, not
    a total cap; pagination follows nextPageToken. Messages without a
    usable HTML body (Notion-Mail and similar formats) are skipped. service
    is injectable for tests; the default is built from settings.
    """
    if until is None:
        until = datetime.now(tz=UTC)

    api = service or build_gmail_service()
    query = f"label:{label} newer_than:{_query_days_back(since)}d"

    fetched: list[FetchedEmail] = []
    page_token: str | None = None
    # Follow nextPageToken so a heavy day isn't silently truncated at one
    # list page.
    while True:
        list_kwargs: dict[str, Any] = {"userId": "me", "q": query, "maxResults": max_results}
        if page_token:
            list_kwargs["pageToken"] = page_token
        list_resp: dict[str, Any] = api.users().messages().list(**list_kwargs).execute()

        for stub in list_resp.get("messages") or []:
            full = api.users().messages().get(userId="me", id=stub["id"], format="full").execute()
            received_at = _parse_received_at(full)
            if received_at < since or received_at >= until:
                continue
            body_html = walk_for_mime(full.get("payload") or {}, "text/html")
            if not body_html.strip():
                continue
            headers = index_headers(full.get("payload") or {})
            fetched.append(
                FetchedEmail(
                    message_id=full["id"],
                    sender_slug=slugify_sender(headers.get("From", "")),
                    subject=headers.get("Subject", ""),
                    received_at=received_at,
                    body_html=body_html,
                )
            )

        page_token = list_resp.get("nextPageToken")
        if not page_token:
            break
    return fetched


def _query_days_back(since: datetime) -> int:
    """Return how many days newer_than: must cover to safely include since.

    Gmail's newer_than: is day-granular. The downstream date filter in
    fetch_news_emails does the precise window, so over-fetching by a day
    is fine.
    """
    delta = datetime.now(tz=UTC) - since
    return max(1, delta.days + 1)


def _parse_received_at(message: dict[str, Any]) -> datetime:
    """Prefer internalDate (epoch ms), then the Date header, then now."""
    return parse_message_received_at(message) or datetime.now(tz=UTC)


# Distinctive substrings that identify a publisher anywhere in the From
# header. The first-pass match catches privaterelay-masked addresses where
# the sender domain is encoded in the local-part (e.g.
# info_at_theinformation_com_<random>@privaterelay.appleid.com) as well as
# display names carrying the publisher's name.
_PUBLISHER_DOMAIN_SUBSTRINGS: tuple[tuple[str, str], ...] = (
    ("theinformation", "theinformation"),
    ("theverge", "theverge"),
    ("bloomberg", "bloomberg"),
    ("nytimes", "nytimes"),
    ("pragmaticengineer", "pragmaticengineer"),
    ("buttondown", "hackernewsletter"),
)

# Brand-only patterns that could collide with random words in email
# local-parts, so they only run against the parsed display name. Covers
# named newsletters that don't carry the publisher domain in their
# display string (DealBook, The Morning, Installer, Optimizer, etc.).
_DISPLAY_BRAND_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"\bnyt\b|new york times|dealbook|today's headlines|the morning|the evening|breaking news",
        "nytimes",
    ),
    (
        r"the\s*verge|vergedaily|installer|optimizer|regulator|the\s*stepback",
        "theverge",
    ),
    (r"pragmatic\s*engineer", "pragmaticengineer"),
    (r"hacker\s*newsletter", "hackernewsletter"),
    (r"the\s*information", "theinformation"),
)


def slugify_sender(from_header: str) -> str:
    """Map a From header to a stable lowercase slug.

    Distinctive publisher substrings match anywhere in the header, which
    catches the iCloud Hide-My-Email pattern where the publisher domain is
    encoded into the privaterelay local-part while the display name is just
    the writer's. Brand patterns match the display name only, to avoid
    false positives in random local-parts. Anything else falls through to
    an alphanumeric slug of the display name.
    """
    lowered = from_header.lower()
    for substring, slug in _PUBLISHER_DOMAIN_SUBSTRINGS:
        if substring in lowered:
            return slug

    name_match = re.match(r"\s*\"?([^<\"]+)\"?\s*<", from_header)
    name = name_match.group(1).strip() if name_match else from_header.strip()
    for pattern, slug in _DISPLAY_BRAND_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            return slug

    cleaned = re.sub(r"[^a-z0-9]", "", name.lower())
    return cleaned or "unknown"
