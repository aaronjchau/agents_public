"""Gmail fetch/parse helpers shared by the scripts/backtest_* family.

Importing this module also bridges NOTION_API_TOKEN to NOTION_TOKEN, so
the backtest scripts must import it before any settings-dependent service
module. Evaluation logic stays in each script.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup

from shared.gmail import walk_for_mime

if TYPE_CHECKING:
    from collections.abc import Sequence

    from googleapiclient.discovery import Resource

# The local .env uses NOTION_API_TOKEN while Settings expects NOTION_TOKEN;
# bridge them at import time, BEFORE anything instantiates Settings, or the
# Settings load crashes.
if "NOTION_TOKEN" not in os.environ and "NOTION_API_TOKEN" in os.environ:
    os.environ["NOTION_TOKEN"] = os.environ["NOTION_API_TOKEN"]


def fetch_labeled_message_stubs(
    service: Resource,
    *,
    label_names: Sequence[str],
    days: int,
    max_results: int,
) -> list[dict[str, Any]]:
    """List message stubs carrying any of label_names within the last N days."""
    label_filter = " OR ".join(f'label:"{name}"' for name in label_names)
    query = f"({label_filter}) newer_than:{days}d"

    list_resp: dict[str, Any] = (
        service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    )
    return list_resp.get("messages") or []


def get_message(service: Resource, message_id: str) -> dict[str, Any]:
    msg: dict[str, Any] = (
        service.users().messages().get(userId="me", id=message_id, format="full").execute()
    )
    return msg


def load_label_id_to_name(service: Resource) -> dict[str, str]:
    """Build {label_id: label_name} for every user label in the inbox."""
    list_resp: dict[str, Any] = service.users().labels().list(userId="me").execute()
    out: dict[str, str] = {}
    for label in list_resp.get("labels") or []:
        lid = label.get("id")
        name = label.get("name")
        if isinstance(lid, str) and isinstance(name, str):
            out[lid] = name
    return out


def extract_body_text(payload: dict[str, Any]) -> str:
    """Prefer text/plain; fall back to BeautifulSoup-stripping the HTML.

    Keeps newline separators (versus the production extractor's spaces)
    so backtest transcripts stay readable when printed.
    """
    plain = walk_for_mime(payload, "text/plain")
    if plain.strip():
        return plain.strip()
    html = walk_for_mime(payload, "text/html")
    if html.strip():
        return BeautifulSoup(html, "html.parser").get_text(separator="\n").strip()
    return ""
