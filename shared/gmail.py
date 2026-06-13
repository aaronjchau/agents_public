"""Gmail API helpers shared across agents.

Owns the Google OAuth client factory plus the message-parsing and
label-materialization helpers every service needs.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from shared.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from googleapiclient.discovery import Resource

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def build_google_credentials() -> Credentials:
    """OAuth credentials from the shared Google refresh-token grant in settings."""
    s = get_settings()
    return Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=s.gmail_refresh_token,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=s.gmail_client_id,
        client_secret=s.gmail_client_secret,
    )


def build_gmail_service() -> Resource:
    """Construct an authorized Gmail v1 API client from settings."""
    return build("gmail", "v1", credentials=build_google_credentials(), cache_discovery=False)


def index_headers(payload: dict[str, Any]) -> dict[str, str]:
    """Build a case-preserving lookup from header name to value."""
    headers: dict[str, str] = {}
    for h in payload.get("headers") or []:
        name = h.get("name")
        value = h.get("value")
        if isinstance(name, str) and isinstance(value, str):
            headers[name] = value
    return headers


def decode_b64url(data: str) -> str:
    """Gmail uses URL-safe base64; pad and decode to UTF-8."""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def walk_for_mime(payload: dict[str, Any], mime_type: str) -> str:
    """Depth-first search the MIME tree; return the first decoded body of mime_type."""
    if payload.get("mimeType") == mime_type:
        data = (payload.get("body") or {}).get("data") or ""
        if data:
            return decode_b64url(data)
    for part in payload.get("parts") or []:
        body = walk_for_mime(part, mime_type)
        if body:
            return body
    return ""


def extract_plain_text(payload: dict[str, Any]) -> str:
    """Return body text suitable for the classifier.

    Prefers a text/plain MIME part; falls back to text/html stripped via
    BeautifulSoup so the classifier never sees raw markup. The depth-first
    walk resolves multipart/alternative containers correctly.
    """
    plain = walk_for_mime(payload, "text/plain")
    if plain.strip():
        return plain
    html = walk_for_mime(payload, "text/html")
    if not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(("script", "style")):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def parse_internal_date(value: str | int | None) -> datetime | None:
    """Convert Gmail's internalDate (epoch ms as string) to a UTC datetime.

    internalDate is when the message reached Gmail's servers, the
    authoritative received time for the audit row; classified_at is the
    wall clock when the DB row was written.
    """
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError):
        return None


def parse_date_header(value: str) -> datetime | None:
    """Parse an RFC 2822 Date header to a UTC datetime; None if malformed."""
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_message_received_at(message: dict[str, Any]) -> datetime | None:
    """Best-effort UTC received time for a full-format Gmail message.

    Prefers internalDate; falls back to the sender-supplied Date header
    (case-insensitive, since header casing varies in forwarded mail).
    Returns None when neither parses.
    """
    received = parse_internal_date(message.get("internalDate"))
    if received is not None:
        return received
    headers = index_headers(message.get("payload") or {})
    for name, value in headers.items():
        if name.lower() == "date":
            return parse_date_header(value)
    return None


def ensure_labels(names: Sequence[str], *, service: Resource | None = None) -> dict[str, str]:
    """Materialize Gmail user labels by name; return {name: id} for names.

    Creates only the labels missing by name, so a second call issues no
    duplicate labels.create calls. System labels are read for the lookup
    but never created or modified.
    """
    api = service or build_gmail_service()

    list_resp: dict[str, Any] = api.users().labels().list(userId="me").execute()
    existing: dict[str, str] = {}
    for label in list_resp.get("labels") or []:
        name = label.get("name")
        label_id = label.get("id")
        if isinstance(name, str) and isinstance(label_id, str):
            existing[name] = label_id

    label_map: dict[str, str] = {}
    for name in names:
        if name in existing:
            label_map[name] = existing[name]
            continue
        created: dict[str, Any] = (
            api.users().labels().create(userId="me", body={"name": name}).execute()
        )
        label_map[name] = created["id"]
    return label_map
