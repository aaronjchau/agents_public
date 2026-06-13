"""Create the daily News Brief page in Notion via the shared ntn wrapper.

The page body is passed via -d <JSON> rather than stdin: ntn api accepts
JSON as a string argument, which keeps the call site simple and avoids a
stdin-pipe dance for what is typically a few KB of markdown.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from shared.brief_format import brief_title
from shared.ntn import run_ntn
from shared.settings import get_settings

if TYPE_CHECKING:
    from datetime import date

NEWS_ICON = "📰"


def write_brief(
    *,
    markdown_body: str,
    brief_date: date,
    data_source_id: str | None = None,
) -> str:
    """Create a News page and return the new page ID.

    The markdown body passes through verbatim (JSON encoding preserves the
    composer's escapes). brief_date is date-only and feeds both the title
    and the Date property. data_source_id=None resolves the News DB id from
    settings at call time, not import, so the module imports without a
    full env.

    Raises:
        shared.ntn.NtnError: if ntn exits non-zero; the message includes
            the captured stderr.
    """
    if data_source_id is None:
        data_source_id = get_settings().notion_news_data_source_id
    body = _build_body(
        markdown_body=markdown_body,
        brief_date=brief_date,
        data_source_id=data_source_id,
    )
    payload = run_ntn(["api", "v1/pages", "-d", json.dumps(body)])
    page_id: str = payload["id"]
    return page_id


def _build_body(*, markdown_body: str, brief_date: date, data_source_id: str) -> dict[str, Any]:
    title = brief_title("News Brief", brief_date)
    iso_date = brief_date.isoformat()
    return {
        "parent": {"data_source_id": data_source_id},
        "icon": {"type": "emoji", "emoji": NEWS_ICON},
        "properties": {
            "Name": {"title": [{"text": {"content": title}}]},
            "Date": {"date": {"start": iso_date}},
        },
        "markdown": markdown_body,
    }
