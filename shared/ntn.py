"""Subprocess wrapper around the ntn CLI, the repo's single Notion access path.

ntn api owns auth and Notion-Version header negotiation; the token is
injected per call via NOTION_API_TOKEN. Tests patch shared.ntn.subprocess.run.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from shared.settings import get_settings

NTN_BIN = "ntn"


class NtnError(Exception):
    """ntn exited non-zero or returned no body; carries exit code and stderr."""

    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"ntn failed (exit {returncode}): {stderr.strip()}")


def run_ntn(args: list[str], *, allow_empty: bool = False) -> dict[str, Any]:
    """Run ntn <args> and parse its stdout as JSON.

    allow_empty=True turns empty stdout into {}; some PATCH endpoints
    reply with no body. By default empty stdout raises, so an empty read
    can't silently impersonate a real payload.

    Raises:
        NtnError: ntn exited non-zero, or stdout was empty without
            allow_empty.
        json.JSONDecodeError: stdout was non-empty but not valid JSON.
    """
    settings = get_settings()
    env = os.environ.copy()
    env["NOTION_API_TOKEN"] = settings.notion_token
    proc = subprocess.run(
        [NTN_BIN, *args],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise NtnError(proc.returncode, proc.stderr)
    if not proc.stdout.strip():
        if allow_empty:
            return {}
        raise NtnError(proc.returncode, "empty stdout (expected a JSON body)")
    payload: dict[str, Any] = json.loads(proc.stdout)
    return payload


def query_data_source(data_source_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Run one data_sources/{id}/query call and return the raw payload."""
    args = ["api", f"v1/data_sources/{data_source_id}/query", "-d", json.dumps(body)]
    return run_ntn(args)


def query_all_pages(
    data_source_id: str,
    body: dict[str, Any],
    *,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    """Query a data source and paginate until has_more is false.

    Notion caps page_size at 100 and several queried sources exceed that,
    so pagination is mandatory. max_pages is a safety stop so a malformed
    cursor never spins forever.
    """
    paginated_body = {**body}
    out: list[dict[str, Any]] = []
    for _ in range(max_pages):
        payload = query_data_source(data_source_id, paginated_body)
        out.extend(page_results(payload))
        if not payload.get("has_more"):
            return out
        next_cursor = payload.get("next_cursor")
        if not isinstance(next_cursor, str):
            return out
        paginated_body = {**body, "start_cursor": next_cursor}
    return out


def page_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the results list out of a query payload, dropping non-dicts."""
    raw = payload.get("results")
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]
