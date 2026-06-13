"""Unit tests for Gmail label CRUD.

The googleapiclient Resource is mocked; tests pin the protocol shape
(arguments and label ids) rather than Gmail's behavior.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from services.triager.labels import (
    FLAGGED_LABEL,
    PRIMARY_LABELS,
    apply_classification,
    ensure_labels_exist,
)


def _service(
    list_response: dict[str, Any],
    *create_responses: dict[str, Any],
    modify_response: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a MagicMock mimicking googleapiclient's chained call shape.

    labels.list returns list_response, labels.create returns the
    create_responses in order, and messages.modify returns modify_response.
    """
    service = MagicMock()
    users_resource = service.users.return_value

    labels_resource = users_resource.labels.return_value
    list_call = MagicMock()
    list_call.execute.return_value = list_response
    labels_resource.list.return_value = list_call

    create_iter = iter(create_responses)
    create_call = MagicMock()
    create_call.execute.side_effect = lambda: next(create_iter)
    labels_resource.create.return_value = create_call

    messages_resource = users_resource.messages.return_value
    modify_call = MagicMock()
    modify_call.execute.return_value = modify_response or {}
    messages_resource.modify.return_value = modify_call

    return service


def _system_label(name: str, label_id: str) -> dict[str, Any]:
    return {"id": label_id, "name": name, "type": "system"}


def _user_label(name: str, label_id: str) -> dict[str, Any]:
    return {"id": label_id, "name": name, "type": "user"}


# ---------------------------------------------------------------- ensure_labels_exist


def test_ensure_labels_exist_creates_all_when_none_present() -> None:
    """Bare mailbox: only system labels exist; the 13 triager labels are all created."""
    list_resp = {
        "labels": [
            _system_label("INBOX", "INBOX"),
            _system_label("SENT", "SENT"),
            _system_label("CATEGORY_PERSONAL", "CATEGORY_PERSONAL"),
            _system_label("CATEGORY_PROMOTIONS", "CATEGORY_PROMOTIONS"),
        ]
    }
    create_responses = [
        {"id": f"Label_{i}", "name": name, "type": "user"}
        for i, name in enumerate((*PRIMARY_LABELS, FLAGGED_LABEL))
    ]
    service = _service(list_resp, *create_responses)

    result = ensure_labels_exist(service=service)

    assert set(result.keys()) == {*PRIMARY_LABELS, FLAGGED_LABEL}
    create_mock = service.users.return_value.labels.return_value.create
    assert create_mock.call_count == 13

    # Every create call should target a managed label by name and use userId=me.
    created_names = [call.kwargs["body"]["name"] for call in create_mock.call_args_list]
    assert set(created_names) == {*PRIMARY_LABELS, FLAGGED_LABEL}
    for call in create_mock.call_args_list:
        assert call.kwargs["userId"] == "me"


def test_ensure_labels_exist_creates_only_missing() -> None:
    """With 5 of 13 present, exactly 8 creates are issued and the map covers all 13."""
    already_present = ("People", "Job Apps", "News", "Marketing", FLAGGED_LABEL)
    list_resp = {
        "labels": [
            _system_label("INBOX", "INBOX"),
            *(_user_label(name, f"Label_existing_{i}") for i, name in enumerate(already_present)),
        ]
    }
    missing = [n for n in (*PRIMARY_LABELS, FLAGGED_LABEL) if n not in already_present]
    create_responses = [
        {"id": f"Label_new_{i}", "name": name, "type": "user"} for i, name in enumerate(missing)
    ]
    service = _service(list_resp, *create_responses)

    result = ensure_labels_exist(service=service)

    create_mock = service.users.return_value.labels.return_value.create
    assert create_mock.call_count == len(missing) == 8
    created_names = [call.kwargs["body"]["name"] for call in create_mock.call_args_list]
    assert set(created_names) == set(missing)

    # Pre-existing labels keep their original IDs in the returned map.
    for i, name in enumerate(already_present):
        assert result[name] == f"Label_existing_{i}"
    assert set(result.keys()) == {*PRIMARY_LABELS, FLAGGED_LABEL}


def test_ensure_labels_exist_does_not_touch_system_labels() -> None:
    """System labels (INBOX / CATEGORY_*) are never created or modified."""
    list_resp = {
        "labels": [
            _system_label("INBOX", "INBOX"),
            _system_label("CATEGORY_PERSONAL", "CATEGORY_PERSONAL"),
            _system_label("CATEGORY_PROMOTIONS", "CATEGORY_PROMOTIONS"),
            _system_label("CATEGORY_UPDATES", "CATEGORY_UPDATES"),
            _system_label("SENT", "SENT"),
            _system_label("DRAFT", "DRAFT"),
            _system_label("SPAM", "SPAM"),
            _system_label("TRASH", "TRASH"),
            _system_label("UNREAD", "UNREAD"),
            _system_label("STARRED", "STARRED"),
            _system_label("IMPORTANT", "IMPORTANT"),
        ]
    }
    create_responses = [
        {"id": f"Label_{i}", "name": name, "type": "user"}
        for i, name in enumerate((*PRIMARY_LABELS, FLAGGED_LABEL))
    ]
    service = _service(list_resp, *create_responses)

    ensure_labels_exist(service=service)

    create_mock = service.users.return_value.labels.return_value.create
    created_names = [call.kwargs["body"]["name"] for call in create_mock.call_args_list]
    for name in created_names:
        assert not name.startswith("CATEGORY_")
        assert name not in {
            "INBOX",
            "SENT",
            "DRAFT",
            "SPAM",
            "TRASH",
            "UNREAD",
            "STARRED",
            "IMPORTANT",
        }


def test_ensure_labels_exist_is_idempotent_on_second_call() -> None:
    """A second call after all 13 exist issues zero create calls."""
    list_resp = {
        "labels": [
            _system_label("INBOX", "INBOX"),
            *(
                _user_label(name, f"Label_{i}")
                for i, name in enumerate((*PRIMARY_LABELS, FLAGGED_LABEL))
            ),
        ]
    }
    service = _service(list_resp)

    ensure_labels_exist(service=service)

    create_mock = service.users.return_value.labels.return_value.create
    assert create_mock.call_count == 0


# ---------------------------------------------------------------- apply_classification


def _label_map_fixture() -> dict[str, str]:
    """All 13 triager labels mapped to deterministic IDs for assertions."""
    label_map: dict[str, str] = {name: f"Label_{i}" for i, name in enumerate(PRIMARY_LABELS)}
    label_map[FLAGGED_LABEL] = "Label_flag"
    return label_map


def test_apply_classification_flagged_adds_primary_and_flag() -> None:
    """flagged=True adds the primary and Flagged ids and removes the other 11 primaries."""
    label_map = _label_map_fixture()
    service = _service({"labels": []})

    apply_classification(
        message_id="msg-123",
        primary_label="Job Apps",
        flagged=True,
        service=service,
        label_map=label_map,
    )

    modify_mock = service.users.return_value.messages.return_value.modify
    assert modify_mock.call_count == 1
    call_kwargs = modify_mock.call_args.kwargs
    assert call_kwargs["userId"] == "me"
    assert call_kwargs["id"] == "msg-123"

    body = call_kwargs["body"]
    add_ids = body["addLabelIds"]
    remove_ids = body["removeLabelIds"]

    # addLabelIds carries Job Apps + Flagged; order does not matter.
    assert set(add_ids) == {label_map["Job Apps"], label_map[FLAGGED_LABEL]}

    # removeLabelIds carries every primary except Job Apps and no system
    # or CATEGORY_* ids.
    expected_removed = {label_map[name] for name in PRIMARY_LABELS if name != "Job Apps"}
    assert set(remove_ids) == expected_removed
    assert len(expected_removed) == 11
    for label_id in remove_ids:
        assert not label_id.startswith("CATEGORY_")
        assert label_id not in {
            "INBOX",
            "SENT",
            "DRAFT",
            "SPAM",
            "TRASH",
            "UNREAD",
            "STARRED",
            "IMPORTANT",
        }


def test_apply_classification_unflagged_removes_flag_label() -> None:
    """Re-classifying a previously-flagged email with flagged=False removes the Flagged ID."""
    label_map = _label_map_fixture()
    service = _service({"labels": []})

    apply_classification(
        message_id="msg-456",
        primary_label="Marketing",
        flagged=False,
        service=service,
        label_map=label_map,
    )

    body = service.users.return_value.messages.return_value.modify.call_args.kwargs["body"]
    add_ids = body["addLabelIds"]
    remove_ids = body["removeLabelIds"]

    assert add_ids == [label_map["Marketing"]]
    # Flagged ID must appear in removeLabelIds so any prior overlay is cleared.
    assert label_map[FLAGGED_LABEL] in remove_ids
    # All other primaries are also removed.
    for name in PRIMARY_LABELS:
        if name != "Marketing":
            assert label_map[name] in remove_ids


def test_apply_classification_lazy_fetches_label_map_when_none() -> None:
    """label_map=None triggers an ensure_labels_exist (labels.list) round-trip."""
    list_resp = {
        "labels": [
            _system_label("INBOX", "INBOX"),
            *(
                _user_label(name, f"Label_{i}")
                for i, name in enumerate((*PRIMARY_LABELS, FLAGGED_LABEL))
            ),
        ]
    }
    service = _service(list_resp)

    apply_classification(
        message_id="msg-789",
        primary_label="News",
        flagged=False,
        service=service,
    )

    list_mock = service.users.return_value.labels.return_value.list
    assert list_mock.call_count == 1
    modify_mock = service.users.return_value.messages.return_value.modify
    assert modify_mock.call_count == 1


def test_apply_classification_with_label_map_skips_lazy_fetch() -> None:
    """When label_map is provided, no labels.list call is issued."""
    label_map = _label_map_fixture()
    service = _service({"labels": []})

    apply_classification(
        message_id="msg-skip",
        primary_label="People",
        flagged=True,
        service=service,
        label_map=label_map,
    )

    list_mock = service.users.return_value.labels.return_value.list
    assert list_mock.call_count == 0
