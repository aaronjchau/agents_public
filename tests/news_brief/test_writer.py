"""Unit tests for the ntn-CLI writer with subprocess mocked.

Tests pin the protocol shape (argument list, JSON body, NOTION_API_TOKEN
propagation) rather than ntn's behavior or Notion's response.
"""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from services.news_brief.writer import (
    NEWS_ICON,
    write_brief,
)
from shared.ntn import NtnError
from shared.settings import get_settings
from tests.conftest import FakeProc

if TYPE_CHECKING:
    from collections.abc import Callable


def _ok_response(page_id: str = "page-abc-123") -> str:
    return json.dumps({"object": "page", "id": page_id})


def _make_run(
    response: FakeProc,
    *,
    capture: list[dict[str, Any]] | None = None,
) -> Callable[..., FakeProc]:
    def _run(args: list[str], **kwargs: Any) -> FakeProc:
        if capture is not None:
            capture.append({"args": args, "kwargs": kwargs})
        response.args = args
        return response

    return _run


# ---------------------------------------------------------------------- success path


def test_write_brief_returns_page_id() -> None:
    fake = _make_run(FakeProc(_ok_response("new-page-xyz")))
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        page_id = write_brief(
            markdown_body='## Hello {color="gray_bg"}\n',
            brief_date=date(2026, 5, 4),
        )
    assert page_id == "new-page-xyz"


def test_write_brief_invokes_ntn_api_v1_pages() -> None:
    captured: list[dict[str, Any]] = []
    fake = _make_run(FakeProc(_ok_response()), capture=captured)
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        write_brief(markdown_body="body", brief_date=date(2026, 5, 4))

    args = captured[0]["args"]
    assert args[0] == "ntn"
    assert args[1] == "api"
    assert args[2] == "v1/pages"
    assert args[3] == "-d"


def test_write_brief_defaults_to_settings_data_source_id() -> None:
    """data_source_id=None resolves the News DB id from settings at call time."""
    captured: list[dict[str, Any]] = []
    fake = _make_run(FakeProc(_ok_response()), capture=captured)
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        write_brief(markdown_body="body", brief_date=date(2026, 5, 4))

    body = json.loads(captured[0]["args"][4])
    expected = get_settings().notion_news_data_source_id
    assert body["parent"] == {"data_source_id": expected}


def test_write_brief_passes_custom_data_source_id() -> None:
    captured: list[dict[str, Any]] = []
    fake = _make_run(FakeProc(_ok_response()), capture=captured)
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        write_brief(
            markdown_body="body",
            brief_date=date(2026, 5, 4),
            data_source_id="abc-other-ds",
        )

    body = json.loads(captured[0]["args"][4])
    assert body["parent"] == {"data_source_id": "abc-other-ds"}


# ---------------------------------------------------------------------- title format


@pytest.mark.parametrize(
    ("brief_date", "expected_title"),
    [
        (date(2026, 5, 4), "News Brief (5/4)"),
        (date(2026, 1, 1), "News Brief (1/1)"),
        (date(2026, 12, 31), "News Brief (12/31)"),
        (date(2026, 9, 9), "News Brief (9/9)"),
        (date(2026, 10, 5), "News Brief (10/5)"),
    ],
)
def test_write_brief_title_uses_non_zero_padded_month_day(
    brief_date: date, expected_title: str
) -> None:
    captured: list[dict[str, Any]] = []
    fake = _make_run(FakeProc(_ok_response()), capture=captured)
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        write_brief(markdown_body="b", brief_date=brief_date)

    body = json.loads(captured[0]["args"][4])
    title_blocks = body["properties"]["Name"]["title"]
    assert title_blocks[0]["text"]["content"] == expected_title


def test_write_brief_date_property_is_iso_date_only() -> None:
    captured: list[dict[str, Any]] = []
    fake = _make_run(FakeProc(_ok_response()), capture=captured)
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        write_brief(markdown_body="b", brief_date=date(2026, 5, 4))

    body = json.loads(captured[0]["args"][4])
    date_value = body["properties"]["Date"]["date"]
    assert date_value == {"start": "2026-05-04"}
    assert "T" not in date_value["start"]


# ---------------------------------------------------------------------- icon


def test_write_brief_sets_news_emoji_icon() -> None:
    captured: list[dict[str, Any]] = []
    fake = _make_run(FakeProc(_ok_response()), capture=captured)
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        write_brief(markdown_body="b", brief_date=date(2026, 5, 4))

    body = json.loads(captured[0]["args"][4])
    assert body["icon"] == {"type": "emoji", "emoji": NEWS_ICON}
    assert NEWS_ICON == "📰"


# ---------------------------------------------------------------------- markdown body


def test_write_brief_passes_markdown_body_verbatim() -> None:
    md = (
        '## AI & Tech {color="gray_bg"}\n'
        "> **OpenAI:** Raised \\$10B at \\$300B valuation. — [The Information](https://x)\n"
    )
    captured: list[dict[str, Any]] = []
    fake = _make_run(FakeProc(_ok_response()), capture=captured)
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        write_brief(markdown_body=md, brief_date=date(2026, 5, 4))

    body = json.loads(captured[0]["args"][4])
    assert body["markdown"] == md


def test_write_brief_preserves_dollar_escapes_through_json_round_trip() -> None:
    """Backslash-escaped dollars must reach Notion as literal \\$, not a bare $."""
    md = "> **Funding:** Series B \\$50M, total \\$120M to date. — [Bloomberg](url)"
    captured: list[dict[str, Any]] = []
    fake = _make_run(FakeProc(_ok_response()), capture=captured)
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        write_brief(markdown_body=md, brief_date=date(2026, 5, 4))

    raw_json = captured[0]["args"][4]
    assert "\\\\$50M" in raw_json
    assert "\\\\$120M" in raw_json

    body = json.loads(raw_json)
    assert "\\$50M" in body["markdown"]
    assert "\\$120M" in body["markdown"]


def test_write_brief_accepts_empty_markdown_body() -> None:
    """An empty body is valid; the caller decides how to handle no-news days."""
    captured: list[dict[str, Any]] = []
    fake = _make_run(FakeProc(_ok_response()), capture=captured)
    with patch("shared.ntn.subprocess.run", side_effect=fake):
        page_id = write_brief(markdown_body="", brief_date=date(2026, 5, 4))

    body = json.loads(captured[0]["args"][4])
    assert body["markdown"] == ""
    assert page_id  # still returns the new page id


# ---------------------------------------------------------------------- env propagation


def test_write_brief_propagates_notion_token_to_subprocess_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "secret_xyz_specific_value")
    get_settings.cache_clear()

    captured: list[dict[str, Any]] = []
    fake = _make_run(FakeProc(_ok_response()), capture=captured)
    try:
        with patch("shared.ntn.subprocess.run", side_effect=fake):
            write_brief(markdown_body="b", brief_date=date(2026, 5, 4))
    finally:
        get_settings.cache_clear()

    env = captured[0]["kwargs"]["env"]
    assert env["NOTION_API_TOKEN"] == "secret_xyz_specific_value"


def test_write_brief_does_not_leak_other_env_vars() -> None:
    """env must be the parent process's env plus NOTION_API_TOKEN, not a stripped env."""
    captured: list[dict[str, Any]] = []
    fake = _make_run(FakeProc(_ok_response()), capture=captured)
    with (
        patch.dict("os.environ", {"PATH": "/custom/path"}, clear=False),
        patch("shared.ntn.subprocess.run", side_effect=fake),
    ):
        write_brief(markdown_body="b", brief_date=date(2026, 5, 4))

    env = captured[0]["kwargs"]["env"]
    assert env["PATH"] == "/custom/path"
    assert "NOTION_API_TOKEN" in env


# ---------------------------------------------------------------------- error path


def test_write_brief_raises_with_stderr_on_nonzero_exit() -> None:
    fake = _make_run(FakeProc("", stderr="auth: invalid token", returncode=1))
    with (
        patch("shared.ntn.subprocess.run", side_effect=fake),
        pytest.raises(NtnError) as excinfo,
    ):
        write_brief(markdown_body="b", brief_date=date(2026, 5, 4))

    err = excinfo.value
    assert err.returncode == 1
    assert err.stderr == "auth: invalid token"
    assert "exit 1" in str(err)


def test_write_brief_raises_on_invalid_json_response() -> None:
    fake = _make_run(FakeProc("not-json-at-all"))
    with (
        patch("shared.ntn.subprocess.run", side_effect=fake),
        pytest.raises(json.JSONDecodeError),
    ):
        write_brief(markdown_body="b", brief_date=date(2026, 5, 4))
