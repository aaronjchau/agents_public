"""Google Calendar API helper shared across agents.

Builds an authorized Calendar v3 client from the same OAuth refresh-token
grant gmail.py uses. Scope caveat: the client always builds, but Calendar
calls 403 until GMAIL_REFRESH_TOKEN carries a Calendar read scope, so it
can ship inert and light up when consent is re-minted.

Design notes: docs/design.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from googleapiclient.discovery import build

from shared.gmail import build_google_credentials

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource


def build_calendar_service() -> Resource:
    """Construct an authorized Google Calendar v3 API client from settings.

    Reuses the shared Google OAuth refresh token. Succeeds regardless of
    the token's granted scopes; whether Calendar reads work depends on
    that consent (see the module docstring).
    """
    return build("calendar", "v3", credentials=build_google_credentials(), cache_discovery=False)
