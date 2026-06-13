"""Gmail sublabel CRUD for the Job Apps pipeline.

Idempotently materializes the seven mutually exclusive sublabels and
swaps any previously assigned one for the newly classified label, never
touching system labels, CATEGORY_* labels, or the Triager-owned Job
Apps primary label. Operates only on the watched account.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, get_args

from services.job_apps.types import Sublabel
from shared.gmail import build_gmail_service, ensure_labels

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

# Sourced from the Sublabel Literal so the classifier and label CRUD
# stay in lockstep; a new sublabel in types.py materializes on the next
# runner pass.
SUBLABELS: tuple[Sublabel, ...] = get_args(Sublabel)


def ensure_sublabels_exist(*, service: Resource | None = None) -> dict[str, str]:
    """Materialize all seven Job Apps sublabels and return a name-to-id map.

    Creates only the labels missing by name; calling twice issues no
    duplicate labels.create calls. System labels and the Triager-owned
    Job Apps primary label are never touched.
    """
    return ensure_labels(SUBLABELS, service=service)


def apply_sublabel(
    *,
    message_id: str,
    sublabel: Sublabel,
    service: Resource | None = None,
    label_map: dict[str, str] | None = None,
) -> None:
    """Apply sublabel to a message, removing any other Job Apps sublabel.

    removeLabelIds carries only the other six sublabel ids; the
    Triager-owned Job Apps primary label and system or CATEGORY_* labels
    are never removed. When label_map is None it is lazily materialized
    via ensure_sublabels_exist; callers classifying many messages should
    pre-warm the map to avoid a labels.list round trip per message.

    TODO(mirror): mirror to the second Gmail account once a refresh
    token for it is added to agents-secrets. The sublabel should land on
    both watched accounts; it currently applies only on the account the
    shared token authenticates.
    """
    if label_map is None:
        label_map = ensure_sublabels_exist(service=service)
    api = service or build_gmail_service()

    add_ids: list[str] = [label_map[sublabel]]
    remove_ids: list[str] = [label_map[name] for name in SUBLABELS if name != sublabel]

    api.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": add_ids, "removeLabelIds": remove_ids},
    ).execute()
