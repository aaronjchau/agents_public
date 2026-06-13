"""Write the Morning Brief to the Briefs Hub via the ntn CLI.

One page per day: when existing_page_id is passed the body is replaced in
place and the TL;DR property refreshed, so the page URL stays stable;
otherwise a new page is created under the Briefs Hub data source.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from services.morning_brief.constants import (
    BRIEF_ICON,
    BRIEF_TYPE,
    BRIEFS_HUB_DATA_SOURCE_ID,
)
from shared.brief_format import brief_title
from shared.ntn import run_ntn

if TYPE_CHECKING:
    from datetime import date


def write_brief(
    *,
    markdown_body: str,
    tldr: str,
    brief_date: date,
    existing_page_id: str | None = None,
    data_source_id: str = BRIEFS_HUB_DATA_SOURCE_ID,
) -> str:
    """Create or overwrite today's Morning brief; return the page id."""
    if existing_page_id is not None:
        _update_brief(existing_page_id, markdown_body=markdown_body, tldr=tldr)
        return existing_page_id
    return _create_brief(
        markdown_body=markdown_body,
        tldr=tldr,
        brief_date=brief_date,
        data_source_id=data_source_id,
    )


def _create_brief(
    *,
    markdown_body: str,
    tldr: str,
    brief_date: date,
    data_source_id: str,
) -> str:
    body = {
        "parent": {"data_source_id": data_source_id},
        "icon": {"type": "emoji", "emoji": BRIEF_ICON},
        "properties": _properties(brief_date=brief_date, tldr=tldr),
        "markdown": markdown_body,
    }
    payload = run_ntn(["api", "v1/pages", "-d", json.dumps(body)])
    page_id = payload.get("id")
    if not isinstance(page_id, str):
        raise RuntimeError(f"ntn create returned no page id: {payload!r}")
    return page_id


def _update_brief(page_id: str, *, markdown_body: str, tldr: str) -> None:
    # Both PATCH endpoints may reply with no body, hence allow_empty.
    content_body = {"type": "replace_content", "replace_content": {"new_str": markdown_body}}
    run_ntn(
        ["api", f"v1/pages/{page_id}/markdown", "-X", "PATCH", "-d", json.dumps(content_body)],
        allow_empty=True,
    )
    # Only TL;DR needs refreshing; the title and Type are already set.
    prop_body = {"properties": {"TL;DR": _rich_text(tldr)}}
    run_ntn(
        ["api", f"v1/pages/{page_id}", "-X", "PATCH", "-d", json.dumps(prop_body)],
        allow_empty=True,
    )


def _properties(*, brief_date: date, tldr: str) -> dict[str, Any]:
    return {
        "Name": {"title": [{"text": {"content": brief_title("Morning Brief", brief_date)}}]},
        "Type": {"select": {"name": BRIEF_TYPE}},
        "TL;DR": _rich_text(tldr),
    }


def _rich_text(text: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": text}}]}
