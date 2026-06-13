"""Tests for the shared ntn subprocess wrapper.

Subprocess is mocked. The tests pin the protocol shape (argv, token
injection into the subprocess env), the NtnError failure contract, and
the pagination loop, not ntn's behavior or Notion's responses.
"""

import json
from unittest.mock import patch

import pytest

from shared import ntn
from shared.ntn import NtnError, page_results, query_all_pages, query_data_source, run_ntn
from shared.settings import get_settings
from tests.conftest import FakeProc, capturing_run

# ----------------------------------------------------------------------- run_ntn


def test_run_ntn_builds_argv_and_injects_token() -> None:
    proc = FakeProc(json.dumps({"object": "page", "id": "p1"}))
    fake_run, captured = capturing_run(proc)

    with patch("shared.ntn.subprocess.run", side_effect=fake_run):
        payload = run_ntn(["api", "v1/pages/p1"])

    assert payload == {"object": "page", "id": "p1"}
    assert captured[0]["cmd"] == ["ntn", "api", "v1/pages/p1"]
    assert captured[0]["env"]["NOTION_API_TOKEN"]


def test_run_ntn_env_is_parent_env_plus_token() -> None:
    fake_run, captured = capturing_run(FakeProc("{}"))

    with (
        patch.dict("os.environ", {"PATH": "/custom/path"}, clear=False),
        patch("shared.ntn.subprocess.run", side_effect=fake_run),
    ):
        run_ntn(["api", "v1/pages/p1"])

    env = captured[0]["env"]
    assert env["PATH"] == "/custom/path"
    assert "NOTION_API_TOKEN" in env


def test_run_ntn_token_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "secret_xyz_specific_value")
    get_settings.cache_clear()

    fake_run, captured = capturing_run(FakeProc("{}"))
    try:
        with patch("shared.ntn.subprocess.run", side_effect=fake_run):
            run_ntn(["api", "v1/pages/p1"])
    finally:
        get_settings.cache_clear()

    assert captured[0]["env"]["NOTION_API_TOKEN"] == "secret_xyz_specific_value"


def test_run_ntn_nonzero_exit_raises_ntn_error() -> None:
    fake_run, _ = capturing_run(FakeProc("", stderr="auth: invalid token\n", returncode=1))

    with (
        patch("shared.ntn.subprocess.run", side_effect=fake_run),
        pytest.raises(NtnError) as excinfo,
    ):
        run_ntn(["api", "v1/pages/p1"])

    err = excinfo.value
    assert err.returncode == 1
    # Raw stderr is preserved on the exception; the message formats it.
    assert err.stderr == "auth: invalid token\n"
    assert str(err) == "ntn failed (exit 1): auth: invalid token"


def test_run_ntn_empty_stdout_raises_by_default() -> None:
    # A GET that returns nothing must fail loudly; downstream readers
    # would otherwise treat {} as a real (empty) page.
    fake_run, _ = capturing_run(FakeProc("  \n"))

    with (
        patch("shared.ntn.subprocess.run", side_effect=fake_run),
        pytest.raises(NtnError, match="empty stdout"),
    ):
        run_ntn(["api", "v1/pages/p1"])


def test_run_ntn_empty_stdout_with_allow_empty_returns_empty_dict() -> None:
    # Some PATCH endpoints reply with no body; tolerant call sites opt in.
    fake_run, _ = capturing_run(FakeProc("  \n"))

    with patch("shared.ntn.subprocess.run", side_effect=fake_run):
        assert run_ntn(["api", "v1/pages/p1", "-X", "PATCH", "-d", "{}"], allow_empty=True) == {}


def test_run_ntn_invalid_json_raises() -> None:
    fake_run, _ = capturing_run(FakeProc("not-json-at-all"))

    with (
        patch("shared.ntn.subprocess.run", side_effect=fake_run),
        pytest.raises(json.JSONDecodeError),
    ):
        run_ntn(["api", "v1/pages/p1"])


# ------------------------------------------------------------------- query helpers


def test_query_data_source_builds_argv_and_parses() -> None:
    body = {"filter": {"property": "Status", "status": {"equals": "Done"}}, "page_size": 100}
    proc = FakeProc(json.dumps({"results": [{"id": "a"}], "has_more": False}))
    fake_run, captured = capturing_run(proc)

    with patch("shared.ntn.subprocess.run", side_effect=fake_run):
        payload = query_data_source("DSID", body)

    assert payload == {"results": [{"id": "a"}], "has_more": False}
    assert captured[0]["cmd"] == [
        "ntn",
        "api",
        "v1/data_sources/DSID/query",
        "-d",
        json.dumps(body),
    ]


def test_query_all_pages_paginates() -> None:
    page1 = FakeProc(json.dumps({"results": [{"id": "1"}], "has_more": True, "next_cursor": "c1"}))
    page2 = FakeProc(json.dumps({"results": [{"id": "2"}], "has_more": False}))
    fake_run, captured = capturing_run(page1, page2)

    with patch("shared.ntn.subprocess.run", side_effect=fake_run):
        rows = query_all_pages("DSID", {"page_size": 100})

    assert [r["id"] for r in rows] == ["1", "2"]
    # Second call threads the cursor as start_cursor.
    second_body = json.loads(captured[1]["cmd"][4])
    assert second_body["start_cursor"] == "c1"
    assert second_body["page_size"] == 100


def test_query_all_pages_stops_at_max_pages() -> None:
    # Always has_more; the safety cap must bound the loop.
    forever = [
        FakeProc(json.dumps({"results": [{"id": str(i)}], "has_more": True, "next_cursor": "c"}))
        for i in range(10)
    ]
    fake_run, captured = capturing_run(*forever)

    with patch("shared.ntn.subprocess.run", side_effect=fake_run):
        rows = query_all_pages("DSID", {"page_size": 100}, max_pages=3)

    assert len(rows) == 3
    assert len(captured) == 3


def test_query_all_pages_stops_on_non_string_cursor() -> None:
    page = FakeProc(json.dumps({"results": [{"id": "1"}], "has_more": True, "next_cursor": None}))
    fake_run, captured = capturing_run(page)

    with patch("shared.ntn.subprocess.run", side_effect=fake_run):
        rows = query_all_pages("DSID", {"page_size": 100})

    assert [r["id"] for r in rows] == ["1"]
    assert len(captured) == 1


def test_page_results_drops_non_dicts() -> None:
    assert page_results({"results": [{"id": "a"}, "junk", 3]}) == [{"id": "a"}]
    assert page_results({"results": "nope"}) == []
    assert page_results({}) == []


def test_ntn_bin_is_ntn() -> None:
    assert ntn.NTN_BIN == "ntn"
