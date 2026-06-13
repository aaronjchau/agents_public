"""Gmail label CRUD for the email triager.

Materializes the 12 primary labels plus the Flagged overlay, and applies
classifications by swapping the previously assigned primary label. A
name-to-id map bridges classifier label names and Gmail's opaque label ids.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, get_args

from services.triager.types import Label
from shared.gmail import build_gmail_service, ensure_labels

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

# All 12 primary labels, sourced from the Label Literal so the classifier
# and label CRUD stay in lockstep: adding a label in types.py materializes
# it on the next triager run. The labels are mutually exclusive; every
# email gets exactly one.
PRIMARY_LABELS: tuple[Label, ...] = get_args(Label)

# Additive overlay applied on top of the primary label when the email
# requires action. The warning emoji is part of the label name itself.
FLAGGED_LABEL = "⚠️ Flagged"

# All labels (12 primary + 1 overlay) the triager owns end-to-end.
_MANAGED_LABELS: tuple[str, ...] = (*PRIMARY_LABELS, FLAGGED_LABEL)


def ensure_labels_exist(*, service: Resource | None = None) -> dict[str, str]:
    """Materialize all 13 triager labels and return a name-to-id map.

    Creates only the labels missing by name, so repeat calls issue no
    duplicate labels.create calls. System labels (INBOX, SENT, CATEGORY_*)
    are never touched; the triager only owns its own user labels.
    """
    return ensure_labels(_MANAGED_LABELS, service=service)


def apply_classification(
    *,
    message_id: str,
    primary_label: Label,
    flagged: bool,
    service: Resource | None = None,
    label_map: dict[str, str] | None = None,
) -> None:
    """Apply primary_label (plus the flagged overlay) to a message.

    Swaps any previously assigned primary label by putting the ids of all
    other primary labels in removeLabelIds; when flagged=False the Flagged
    id is also removed in case the message was previously flagged. System
    and CATEGORY_* labels never enter removeLabelIds. When label_map is
    None it is lazily fetched via ensure_labels_exist; callers classifying
    many messages should pre-warm and pass it in to avoid one labels.list
    round-trip per message.
    """
    if label_map is None:
        label_map = ensure_labels_exist(service=service)
    api = service or build_gmail_service()

    primary_id = label_map[primary_label]
    flagged_id = label_map[FLAGGED_LABEL]

    add_ids: list[str] = [primary_id]
    if flagged:
        add_ids.append(flagged_id)

    remove_ids: list[str] = [label_map[name] for name in PRIMARY_LABELS if name != primary_label]
    if not flagged:
        remove_ids.append(flagged_id)

    api.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": add_ids, "removeLabelIds": remove_ids},
    ).execute()
