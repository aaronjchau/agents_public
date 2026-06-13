"""Tests for the morning-brief-specific markdown fetch; the shared ntn
wrapper's own behavior is covered in tests/shared/test_ntn.py."""

import json
from unittest.mock import patch

from services.morning_brief import notion_reader
from tests.conftest import FakeProc, capturing_run


def test_fetch_page_markdown_returns_markdown_field() -> None:
    proc = FakeProc(
        json.dumps({"object": "page_markdown", "id": "p1", "markdown": "# Hi", "truncated": False})
    )
    fake_run, captured = capturing_run(proc)

    with patch("shared.ntn.subprocess.run", side_effect=fake_run):
        markdown = notion_reader.fetch_page_markdown("p1")

    assert markdown == "# Hi"
    assert captured[0]["cmd"] == ["ntn", "api", "v1/pages/p1/markdown"]


def test_fetch_page_markdown_missing_field_returns_empty() -> None:
    proc = FakeProc(json.dumps({"object": "page_markdown", "id": "p1"}))
    fake_run, _ = capturing_run(proc)

    with patch("shared.ntn.subprocess.run", side_effect=fake_run):
        assert notion_reader.fetch_page_markdown("p1") == ""
