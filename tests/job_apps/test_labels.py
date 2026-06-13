"""Tests for Job Apps Gmail sublabel CRUD.

The googleapiclient Resource is mocked; tests pin protocol shape (which
ids land in addLabelIds vs removeLabelIds), not Gmail behavior.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from services.job_apps.labels import (
    SUBLABELS,
    apply_sublabel,
    ensure_sublabels_exist,
)


def _service(
    list_response: dict[str, Any],
    *create_responses: dict[str, Any],
    modify_response: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a MagicMock mimicking googleapiclient's chained call shape."""
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


# ---------------------------------------------------------------- ensure_sublabels_exist


def test_ensure_sublabels_exist_creates_all_when_none_present() -> None:
    """Bare mailbox: only system labels exist; the 7 sublabels are all created."""
    list_resp = {
        "labels": [
            _system_label("INBOX", "INBOX"),
            _system_label("SENT", "SENT"),
            _system_label("CATEGORY_PERSONAL", "CATEGORY_PERSONAL"),
            _system_label("CATEGORY_PROMOTIONS", "CATEGORY_PROMOTIONS"),
            # Pre-existing Triager-owned labels; must not be re-created or removed.
            _user_label("Job Apps", "Label_jobapps"),
            _user_label("News", "Label_news"),
        ]
    }
    create_responses = [
        {"id": f"Label_{i}", "name": name, "type": "user"} for i, name in enumerate(SUBLABELS)
    ]
    service = _service(list_resp, *create_responses)

    result = ensure_sublabels_exist(service=service)

    assert set(result.keys()) == set(SUBLABELS)
    create_mock = service.users.return_value.labels.return_value.create
    assert create_mock.call_count == 7

    # Every create call should target a sublabel by name and use userId=me.
    created_names = [call.kwargs["body"]["name"] for call in create_mock.call_args_list]
    assert set(created_names) == set(SUBLABELS)
    for call in create_mock.call_args_list:
        assert call.kwargs["userId"] == "me"

    # The Triager's "Job Apps" primary label was already present and must
    # not be re-created.
    assert "Job Apps" not in created_names


def test_ensure_sublabels_exist_creates_only_missing() -> None:
    """Three existing sublabels mean exactly four create calls; the map covers all seven."""
    already_present = ("Offer", "Rejection", "Status Update")
    list_resp = {
        "labels": [
            _system_label("INBOX", "INBOX"),
            _user_label("Job Apps", "Label_jobapps"),  # Triager-owned.
            *(_user_label(name, f"Label_existing_{i}") for i, name in enumerate(already_present)),
        ]
    }
    missing = [n for n in SUBLABELS if n not in already_present]
    create_responses = [
        {"id": f"Label_new_{i}", "name": name, "type": "user"} for i, name in enumerate(missing)
    ]
    service = _service(list_resp, *create_responses)

    result = ensure_sublabels_exist(service=service)

    create_mock = service.users.return_value.labels.return_value.create
    assert create_mock.call_count == len(missing) == 4
    created_names = [call.kwargs["body"]["name"] for call in create_mock.call_args_list]
    assert set(created_names) == set(missing)

    # Pre-existing sublabels keep their original IDs in the returned map.
    for i, name in enumerate(already_present):
        assert result[name] == f"Label_existing_{i}"
    assert set(result.keys()) == set(SUBLABELS)


def test_ensure_sublabels_exist_does_not_touch_system_or_other_labels() -> None:
    """System / CATEGORY_* / unrelated user labels are never created or modified."""
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
            # Triager primary labels; pre-existing, must not be touched.
            _user_label("Job Apps", "Label_jobapps"),
            _user_label("News", "Label_news"),
            _user_label("Marketing", "Label_marketing"),
            _user_label("⚠️ Flagged", "Label_flag"),
        ]
    }
    create_responses = [
        {"id": f"Label_{i}", "name": name, "type": "user"} for i, name in enumerate(SUBLABELS)
    ]
    service = _service(list_resp, *create_responses)

    ensure_sublabels_exist(service=service)

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
            # Triager-owned labels are also never re-created.
            "Job Apps",
            "News",
            "Marketing",
            "⚠️ Flagged",
        }


def test_ensure_sublabels_exist_is_idempotent_on_second_call() -> None:
    """A second call with all seven present issues zero create calls."""
    list_resp = {
        "labels": [
            _system_label("INBOX", "INBOX"),
            *(_user_label(name, f"Label_{i}") for i, name in enumerate(SUBLABELS)),
        ]
    }
    service = _service(list_resp)

    ensure_sublabels_exist(service=service)

    create_mock = service.users.return_value.labels.return_value.create
    assert create_mock.call_count == 0


# ---------------------------------------------------------------- apply_sublabel


def _label_map_fixture() -> dict[str, str]:
    """All 7 Job Apps sublabels mapped to deterministic IDs for assertions."""
    return {name: f"Label_{i}" for i, name in enumerate(SUBLABELS)}


def test_apply_sublabel_adds_target_and_removes_other_six() -> None:
    """The target sublabel id is added and the other six sublabel ids are removed."""
    label_map = _label_map_fixture()
    service = _service({"labels": []})

    apply_sublabel(
        message_id="msg-123",
        sublabel="Interview Scheduling",
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

    assert add_ids == [label_map["Interview Scheduling"]]

    expected_removed = {label_map[name] for name in SUBLABELS if name != "Interview Scheduling"}
    assert set(remove_ids) == expected_removed
    assert len(expected_removed) == 6


def test_apply_sublabel_never_touches_top_level_job_apps_label() -> None:
    """The Triager-owned Job Apps label id never appears in addLabelIds or removeLabelIds.

    Removing it would silently un-classify the message.
    """
    label_map = _label_map_fixture()
    service = _service({"labels": []})

    apply_sublabel(
        message_id="msg-jobapps-untouched",
        sublabel="Application Confirmation",
        service=service,
        label_map=label_map,
    )

    body = service.users.return_value.messages.return_value.modify.call_args.kwargs["body"]
    add_ids = body["addLabelIds"]
    remove_ids = body["removeLabelIds"]

    # "Job Apps" is not a Sublabel, so its id is absent from the sublabel
    # label_map and cannot appear in either list; check defensively via
    # the canonical Triager label id used elsewhere in tests.
    assert "Label_jobapps" not in add_ids
    assert "Label_jobapps" not in remove_ids


def test_apply_sublabel_does_not_touch_system_or_category_labels() -> None:
    """No system labels (INBOX, SENT) or CATEGORY_* labels in addLabelIds or removeLabelIds."""
    label_map = _label_map_fixture()
    service = _service({"labels": []})

    apply_sublabel(
        message_id="msg-sys",
        sublabel="Rejection",
        service=service,
        label_map=label_map,
    )

    body = service.users.return_value.messages.return_value.modify.call_args.kwargs["body"]
    for label_id in (*body["addLabelIds"], *body["removeLabelIds"]):
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


def test_apply_sublabel_lazy_fetches_label_map_when_none() -> None:
    """label_map=None triggers an ensure_sublabels_exist (labels.list) round-trip."""
    list_resp = {
        "labels": [
            _system_label("INBOX", "INBOX"),
            *(_user_label(name, f"Label_{i}") for i, name in enumerate(SUBLABELS)),
        ]
    }
    service = _service(list_resp)

    apply_sublabel(
        message_id="msg-789",
        sublabel="Assessment",
        service=service,
    )

    list_mock = service.users.return_value.labels.return_value.list
    assert list_mock.call_count == 1
    modify_mock = service.users.return_value.messages.return_value.modify
    assert modify_mock.call_count == 1


def test_apply_sublabel_with_label_map_skips_lazy_fetch() -> None:
    """When label_map is provided, no labels.list call is issued."""
    label_map = _label_map_fixture()
    service = _service({"labels": []})

    apply_sublabel(
        message_id="msg-skip",
        sublabel="Recruiter Outreach",
        service=service,
        label_map=label_map,
    )

    list_mock = service.users.return_value.labels.return_value.list
    assert list_mock.call_count == 0
