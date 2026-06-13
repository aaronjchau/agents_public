"""Tests for the Morning Brief Gmail fetcher."""

from typing import Any
from unittest.mock import MagicMock

from services.morning_brief import constants
from services.morning_brief.gmail_fetcher import _sender_display, fetch_emails


def _msg(from_value: str, subject: str) -> dict[str, Any]:
    return {
        "payload": {
            "headers": [
                {"name": "From", "value": from_value},
                {"name": "Subject", "value": subject},
            ]
        }
    }


def _service(
    list_responses: list[dict[str, Any]], get_responses: list[dict[str, Any]]
) -> MagicMock:
    service = MagicMock()
    messages = service.users.return_value.messages.return_value

    list_call = MagicMock()
    list_call.execute.side_effect = list(list_responses)
    messages.list.return_value = list_call

    get_iter = iter(get_responses)
    get_call = MagicMock()
    get_call.execute.side_effect = lambda: next(get_iter)
    messages.get.return_value = get_call

    return service


def test_fetch_emails_dedupes_by_priority() -> None:
    # One list response per category, in priority order. Security (idx 1)
    # surfaces m1; Notifications (idx 8) re-surfaces m1 (skipped) plus m2.
    n = len(constants.EMAIL_CATEGORIES)
    list_responses: list[dict[str, Any]] = [{"messages": []} for _ in range(n)]
    list_responses[1] = {"messages": [{"id": "m1"}]}
    list_responses[8] = {"messages": [{"id": "m1"}, {"id": "m2"}]}
    get_responses = [
        _msg("Chase <no-reply@chase.com>", "Payment posted"),
        _msg("GitHub <n@gh.com>", "PR merged"),
    ]

    service = _service(list_responses, get_responses)
    emails = fetch_emails(service=service)

    assert len(emails) == 2
    assert emails[0].message_id == "m1"
    assert emails[0].sender == "Chase"
    assert emails[0].subject == "Payment posted"
    assert emails[0].category_priority == 2  # Security
    assert emails[1].message_id == "m2"
    assert emails[1].sender == "GitHub"
    assert emails[1].category_priority == 9  # Notifications
    # get() is called only twice: the duplicate m1 was skipped before fetch.
    assert service.users.return_value.messages.return_value.get.return_value.execute.call_count == 2


def test_fetch_emails_empty() -> None:
    n = len(constants.EMAIL_CATEGORIES)
    service = _service([{"messages": []} for _ in range(n)], [])
    assert fetch_emails(service=service) == []


def test_sender_display_variants() -> None:
    assert _sender_display("Amazon <ship@amazon.com>") == "Amazon"
    assert _sender_display('"LinkedIn News" <news@linkedin.com>') == "LinkedIn News"
    assert _sender_display("bare@example.com") == "bare@example.com"
    assert _sender_display("<only@addr.com>") == "only@addr.com"
