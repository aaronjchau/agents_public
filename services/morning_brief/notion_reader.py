"""Notion read helpers for the Morning Brief, via the shared ntn wrapper.

Re-exports the paginated query_all_pages (several sources exceed Notion's
100-row page cap) and adds the page-markdown fetch for the News page.
"""

from __future__ import annotations

from shared.ntn import query_all_pages, run_ntn

__all__ = ["fetch_page_markdown", "query_all_pages"]


def fetch_page_markdown(page_id: str) -> str:
    """Return a page's content as enhanced Markdown, or "" if absent."""
    payload = run_ntn(["api", f"v1/pages/{page_id}/markdown"])
    markdown = payload.get("markdown")
    return markdown if isinstance(markdown, str) else ""
