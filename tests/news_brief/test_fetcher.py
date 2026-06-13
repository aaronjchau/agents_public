"""Unit tests for the Gmail-API fetcher with the googleapiclient Resource mocked.

Tests pin protocol shape (arguments to list/get, response interpretation,
the date window) rather than Gmail's behavior.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.news_brief.fetcher import (
    FetchedEmail,
    fetch_news_emails,
    slugify_sender,
)


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).rstrip(b"=").decode("ascii")


def _msg(
    *,
    msg_id: str,
    from_header: str = "Bloomberg <x@y.com>",
    subject: str = "AM",
    received_at: datetime = datetime(2026, 5, 3, 10, 0, 0, tzinfo=UTC),
    body_html: str = "<html>real</html>",
    extra_parts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a Gmail-API-shaped message payload for tests."""
    parts: list[dict[str, Any]] = []
    if extra_parts:
        parts.extend(extra_parts)
    if body_html:
        parts.append(
            {
                "mimeType": "text/html",
                "body": {"data": _b64url(body_html)},
            }
        )

    return {
        "id": msg_id,
        "internalDate": str(int(received_at.timestamp() * 1000)),
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": from_header},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": received_at.strftime("%a, %d %b %Y %H:%M:%S +0000")},
            ],
            "parts": parts,
        },
    }


def _service(list_response: dict[str, Any], *get_responses: dict[str, Any]) -> MagicMock:
    """Build a MagicMock mimicking googleapiclient's chained call shape.

    list(...).execute() yields list_response; each get(...).execute()
    yields the next item of get_responses.
    """
    service = MagicMock()
    users_resource = service.users.return_value
    messages_resource = users_resource.messages.return_value

    list_call = MagicMock()
    list_call.execute.return_value = list_response
    messages_resource.list.return_value = list_call

    get_iter = iter(get_responses)
    get_call = MagicMock()
    get_call.execute.side_effect = lambda: next(get_iter)
    messages_resource.get.return_value = get_call

    return service


# ---------------------------------------------------------------- slugify


@pytest.mark.parametrize(
    ("from_header", "expected"),
    [
        ("Bloomberg <noreply@news.bloomberg.com>", "bloomberg"),
        ('"NYT The Morning" <nytdirect@nytimes.com>', "nytimes"),
        ("DealBook <dealbook-noreply@nytimes.com>", "nytimes"),
        ("The Information <hello@theinformation.com>", "theinformation"),
        ("The Verge <noreply@theverge.com>", "theverge"),
        ("Installer <installer@theverge.com>", "theverge"),
        ("Pragmatic Engineer <newsletter@substack.com>", "pragmaticengineer"),
        ("Hacker Newsletter <hn@buttondown.com>", "hackernewsletter"),
        (
            "Bloomberg <noreply_at_news_bloomberg_com_xyz@privaterelay.appleid.com>",
            "bloomberg",
        ),
        # Edge cases seen in production:
        # privaterelay encodes the publisher domain in the local-part, but
        # the display name is the writer's, not the publication's.
        (
            "Jane Sample <info_at_theinformation_com_a1b2c3d4@privaterelay.appleid.com>",
            "theinformation",
        ),
        (
            "Riley Sample <hello_at_theinformation_com_e5f6a7b8@privaterelay.appleid.com>",
            "theinformation",
        ),
        (
            '"Pat O\'Sample, The Verge" <thestepback_at_theverge_com_xyz@privaterelay.appleid.com>',
            "theverge",
        ),
        (
            "Alex Sample from Installer <installer_at_theverge_com_xyz@privaterelay.appleid.com>",
            "theverge",
        ),
    ],
)
def test_slugify_sender_known_publishers(from_header: str, expected: str) -> None:
    assert slugify_sender(from_header) == expected


def test_slugify_sender_unknown_falls_back_to_alphanumeric_name() -> None:
    assert slugify_sender("Some Random Newsletter <hi@example.com>") == "somerandomnewsletter"


def test_slugify_sender_no_angle_brackets() -> None:
    assert slugify_sender("noreply@example.com") == "noreplyexamplecom"


# ------------------------------------------------------- fetch_news_emails


def test_fetch_news_emails_returns_empty_when_list_is_empty() -> None:
    service = _service({"messages": []})
    result = fetch_news_emails(since=datetime(2026, 5, 1, tzinfo=UTC), service=service)
    assert result == []


def test_fetch_news_emails_returns_full_message_when_in_window() -> None:
    msg = _msg(msg_id="abc")
    service = _service({"messages": [{"id": "abc"}]}, msg)

    result = fetch_news_emails(since=datetime(2026, 5, 1, tzinfo=UTC), service=service)

    assert len(result) == 1
    fetched = result[0]
    assert isinstance(fetched, FetchedEmail)
    assert fetched.message_id == "abc"
    assert fetched.sender_slug == "bloomberg"
    assert fetched.subject == "AM"
    assert "real" in fetched.body_html
    assert fetched.received_at == datetime(2026, 5, 3, 10, 0, 0, tzinfo=UTC)


def test_fetch_news_emails_filters_outside_window() -> None:
    old = _msg(
        msg_id="old",
        subject="stale",
        received_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    new = _msg(msg_id="new", subject="fresh")
    service = _service(
        {"messages": [{"id": "old"}, {"id": "new"}]},
        old,
        new,
    )

    result = fetch_news_emails(since=datetime(2026, 5, 1, tzinfo=UTC), service=service)

    assert [m.message_id for m in result] == ["new"]


def test_fetch_news_emails_skips_message_with_empty_html() -> None:
    msg = _msg(msg_id="notion", body_html="")
    service = _service({"messages": [{"id": "notion"}]}, msg)

    result = fetch_news_emails(since=datetime(2026, 5, 1, tzinfo=UTC), service=service)

    assert result == []


def test_fetch_news_emails_passes_label_in_query() -> None:
    service = _service({"messages": []})
    fetch_news_emails(
        since=datetime(2026, 5, 1, tzinfo=UTC),
        label="Reading",
        service=service,
    )

    list_kwargs = service.users.return_value.messages.return_value.list.call_args.kwargs
    assert list_kwargs["userId"] == "me"
    assert "label:Reading" in list_kwargs["q"]


def test_fetch_news_emails_until_excludes_upper_bound() -> None:
    edge = _msg(
        msg_id="edge",
        received_at=datetime(2026, 5, 4, 0, 0, 0, tzinfo=UTC),
    )
    service = _service({"messages": [{"id": "edge"}]}, edge)

    result = fetch_news_emails(
        since=datetime(2026, 5, 1, tzinfo=UTC),
        until=datetime(2026, 5, 4, tzinfo=UTC),
        service=service,
    )

    assert result == []


def test_fetch_news_emails_default_until_is_now() -> None:
    far_future = datetime.now(tz=UTC) + timedelta(days=365)
    msg = _msg(msg_id="future", received_at=far_future)
    service = _service({"messages": [{"id": "future"}]}, msg)

    result = fetch_news_emails(since=datetime(2026, 5, 1, tzinfo=UTC), service=service)

    assert result == []


def test_fetch_news_emails_follows_next_page_token() -> None:
    """A multi-page list response is drained fully, threading pageToken."""
    page1 = {"messages": [{"id": "m1"}], "nextPageToken": "tok-2"}
    page2 = {"messages": [{"id": "m2"}]}

    service = MagicMock()
    messages_resource = service.users.return_value.messages.return_value
    list_call = MagicMock()
    list_call.execute.side_effect = [page1, page2]
    messages_resource.list.return_value = list_call
    get_iter = iter([_msg(msg_id="m1"), _msg(msg_id="m2")])
    get_call = MagicMock()
    get_call.execute.side_effect = lambda: next(get_iter)
    messages_resource.get.return_value = get_call

    result = fetch_news_emails(since=datetime(2026, 5, 1, tzinfo=UTC), service=service)

    assert [m.message_id for m in result] == ["m1", "m2"]
    list_calls = messages_resource.list.call_args_list
    assert "pageToken" not in list_calls[0].kwargs
    assert list_calls[1].kwargs["pageToken"] == "tok-2"


def test_fetch_news_emails_descends_multipart_to_find_html() -> None:
    """Bloomberg-style multipart/alternative with text/plain first, html nested."""
    text_part = {
        "mimeType": "text/plain",
        "body": {"data": _b64url("plain fallback")},
    }
    msg = _msg(msg_id="bbg", extra_parts=[text_part])
    service = _service({"messages": [{"id": "bbg"}]}, msg)

    result = fetch_news_emails(since=datetime(2026, 5, 1, tzinfo=UTC), service=service)

    assert len(result) == 1
    assert "real" in result[0].body_html


def test_fetch_news_emails_falls_back_to_date_header_when_internal_date_missing() -> None:
    """internalDate is preferred but the Date header is the documented fallback."""
    msg = _msg(msg_id="fallback")
    msg.pop("internalDate")
    service = _service({"messages": [{"id": "fallback"}]}, msg)

    result = fetch_news_emails(since=datetime(2026, 5, 1, tzinfo=UTC), service=service)

    assert len(result) == 1
    assert result[0].received_at == datetime(2026, 5, 3, 10, 0, 0, tzinfo=UTC)
