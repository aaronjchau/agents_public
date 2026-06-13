"""Tests for the Morning Brief Notion writer."""

import json
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest

from services.morning_brief import writer
from services.morning_brief.constants import BRIEFS_HUB_DATA_SOURCE_ID
from shared.ntn import NtnError
from tests.conftest import FakeProc, capturing_run

BRIEF_DATE = date(2026, 6, 4)


def test_create_brief_posts_page_with_markdown() -> None:
    proc = FakeProc(json.dumps({"id": "new-page-1"}))
    fake_run, captured = capturing_run(proc)

    with patch("shared.ntn.subprocess.run", side_effect=fake_run):
        page_id = writer.write_brief(
            markdown_body="## Body",
            tldr="Short summary.",
            brief_date=BRIEF_DATE,
        )

    assert page_id == "new-page-1"
    cmd = captured[0]["cmd"]
    assert cmd[:3] == ["ntn", "api", "v1/pages"]
    body = json.loads(cmd[4])
    assert body["parent"] == {"data_source_id": BRIEFS_HUB_DATA_SOURCE_ID}
    assert body["icon"] == {"type": "emoji", "emoji": "☀️"}
    assert body["markdown"] == "## Body"
    assert body["properties"]["Name"]["title"][0]["text"]["content"] == "Morning Brief (6/4)"
    assert body["properties"]["Type"] == {"select": {"name": "Morning"}}
    assert body["properties"]["TL;DR"]["rich_text"][0]["text"]["content"] == "Short summary."


def test_update_brief_replaces_content_and_refreshes_tldr() -> None:
    # Two PATCH calls: markdown replace, then property update.
    fake_run, captured = capturing_run(FakeProc("{}"), FakeProc("{}"))

    with patch("shared.ntn.subprocess.run", side_effect=fake_run):
        page_id = writer.write_brief(
            markdown_body="## New Body",
            tldr="Updated summary.",
            brief_date=BRIEF_DATE,
            existing_page_id="existing-1",
        )

    assert page_id == "existing-1"
    # 1) Markdown replace_content.
    assert captured[0]["cmd"][:5] == ["ntn", "api", "v1/pages/existing-1/markdown", "-X", "PATCH"]
    content = json.loads(captured[0]["cmd"][6])
    assert content == {"type": "replace_content", "replace_content": {"new_str": "## New Body"}}
    # 2) TL;DR property refresh.
    assert captured[1]["cmd"][:5] == ["ntn", "api", "v1/pages/existing-1", "-X", "PATCH"]
    props = json.loads(captured[1]["cmd"][6])
    assert props["properties"]["TL;DR"]["rich_text"][0]["text"]["content"] == "Updated summary."


def test_update_brief_tolerates_empty_ntn_stdout() -> None:
    # The PATCH endpoints can reply with no body; the update path opts
    # into the wrapper's allow_empty guard instead of erroring.
    fake_run, _ = capturing_run(FakeProc(""), FakeProc(""))

    with patch("shared.ntn.subprocess.run", side_effect=fake_run):
        page_id = writer.write_brief(
            markdown_body="x",
            tldr="y",
            brief_date=BRIEF_DATE,
            existing_page_id="existing-1",
        )

    assert page_id == "existing-1"


def test_create_missing_id_raises() -> None:
    fake_run, _ = capturing_run(FakeProc(json.dumps({"object": "page"})))
    with (
        patch("shared.ntn.subprocess.run", side_effect=fake_run),
        pytest.raises(RuntimeError, match="no page id"),
    ):
        writer.write_brief(markdown_body="x", tldr="y", brief_date=BRIEF_DATE)


def test_write_brief_raises_on_nonzero_exit() -> None:
    fake_run, _ = capturing_run(FakeProc("", stderr="bad request", returncode=1))
    with (
        patch("shared.ntn.subprocess.run", side_effect=fake_run),
        pytest.raises(NtnError),
    ):
        writer.write_brief(markdown_body="x", tldr="y", brief_date=BRIEF_DATE)


def test_token_injected_into_env() -> None:
    proc = FakeProc(json.dumps({"id": "p"}))
    captured_env: dict[str, Any] = {}

    def _run(cmd: list[str], **kwargs: Any) -> FakeProc:
        captured_env.update(kwargs.get("env") or {})
        return proc

    with patch("shared.ntn.subprocess.run", side_effect=_run):
        writer.write_brief(markdown_body="x", tldr="y", brief_date=BRIEF_DATE)

    assert captured_env.get("NOTION_API_TOKEN")
