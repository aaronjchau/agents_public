"""Backtest the triager classifier against past labeled mail.

Pulls the last N days of mail labeled with one of the 12 primary triager
labels, runs the classifier blind to those labels, and prints overall
agreement plus a confusion matrix and Flagged precision/recall. Use it
to validate prompt or model changes before deploying.

    uv run python scripts/backtest_triager.py --days 7 --max 50

Cost: one Sonnet classifier call per email, roughly $0.005 to $0.02
each. Reads ANTHROPIC_API_KEY and GMAIL_* via Settings; the local .env's
NOTION_API_TOKEN is bridged to NOTION_TOKEN at import.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from typing import Any

# Importing _backtest_common bridges NOTION_API_TOKEN to NOTION_TOKEN
# before anything below instantiates Settings.
from _backtest_common import (
    extract_body_text,
    fetch_labeled_message_stubs,
    get_message,
    load_label_id_to_name,
)

from services.triager.classifier import classify
from services.triager.labels import FLAGGED_LABEL, PRIMARY_LABELS
from shared.gmail import build_gmail_service, index_headers

PRIMARY_LABEL_NAMES: set[str] = set(PRIMARY_LABELS)


def actual_primary_label(message: dict[str, Any], id_to_name: dict[str, str]) -> str | None:
    """Return the first primary triager label currently on a message, or None.

    Labels are exclusive in practice; scanning rather than asserting
    tolerates historical drift.
    """
    for label_id in message.get("labelIds") or []:
        name = id_to_name.get(label_id)
        if name in PRIMARY_LABEL_NAMES:
            return name
    return None


def actual_flagged(message: dict[str, Any], id_to_name: dict[str, str]) -> bool:
    return any(
        id_to_name.get(label_id) == FLAGGED_LABEL for label_id in message.get("labelIds") or []
    )


def _format_confusion(confusion: dict[str, Counter[str]]) -> str:
    """Render the confusion matrix as a fixed-width text table."""
    cols = list(PRIMARY_LABELS)
    lines: list[str] = []
    header = f"  {'actual\\pred':18s} | " + " | ".join(f"{c[:6]:>6s}" for c in cols)
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for actual in cols:
        row = confusion.get(actual, Counter())
        if not row:
            continue
        cells = " | ".join((f"{row[p]:>6d}" if row[p] else f"{'.':>6s}") for p in cols)
        lines.append(f"  {actual:18s} | {cells}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backtest the new triager classifier against past labeled mail."
    )
    parser.add_argument("--days", type=int, default=7, help="Days back to scan (default: 7)")
    parser.add_argument(
        "--max", type=int, default=50, help="Max messages to backtest (default: 50)"
    )
    parser.add_argument(
        "--show-disagreements",
        type=int,
        default=10,
        help="How many disagreements to detail in the report (default: 10)",
    )
    args = parser.parse_args()

    service = build_gmail_service()
    id_to_name = load_label_id_to_name(service)

    print(f"Backtesting up to {args.max} messages from the last {args.days} days...")

    stubs = fetch_labeled_message_stubs(
        service, label_names=PRIMARY_LABELS, days=args.days, max_results=args.max
    )
    print(f"Gmail returned {len(stubs)} candidate messages.\n")

    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    flagged_correct = 0
    flagged_total = 0
    disagreements: list[dict[str, str]] = []
    skipped_no_label = 0
    skipped_no_body = 0
    skipped_error = 0

    for i, stub in enumerate(stubs, 1):
        msg = get_message(service, stub["id"])
        actual = actual_primary_label(msg, id_to_name)
        if actual is None:
            skipped_no_label += 1
            continue

        actual_flag = actual_flagged(msg, id_to_name)
        headers = index_headers(msg.get("payload") or {})
        sender = headers.get("From", "")
        subject = headers.get("Subject", "")
        body = extract_body_text(msg.get("payload") or {})

        if not body:
            skipped_no_body += 1
            continue

        try:
            classification = classify(sender=sender, subject=subject, body_text=body).classification
        except Exception as exc:
            print(
                f"  [{i:3d}/{len(stubs)}] CLASSIFY-ERR {subject[:40]!r}: {exc}",
                file=sys.stderr,
            )
            skipped_error += 1
            continue

        predicted = classification.primary_label
        confusion[actual][predicted] += 1
        if classification.flagged == actual_flag:
            flagged_correct += 1
        flagged_total += 1

        match = "OK" if predicted == actual else "DIFF"
        flag_match = "F" if actual_flag == classification.flagged else "f"
        print(
            f"  [{i:3d}/{len(stubs)}] {match:4s} {flag_match} "
            f"actual={actual!r:<14} pred={predicted!r:<14} {subject[:50]!r}"
        )

        if predicted != actual:
            disagreements.append(
                {
                    "subject": subject[:80],
                    "sender": sender[:60],
                    "actual": actual,
                    "predicted": predicted,
                    "reasoning": classification.reasoning,
                }
            )

    total = sum(sum(row.values()) for row in confusion.values())
    correct = sum(row[actual] for actual, row in confusion.items())
    print("\n" + "=" * 72)
    print("BACKTEST RESULTS")
    print("=" * 72)
    print(
        f"Classified: {total}  "
        f"Skipped(no-label): {skipped_no_label}  "
        f"Skipped(no-body): {skipped_no_body}  "
        f"Skipped(error): {skipped_error}"
    )
    if total:
        print(f"Top-1 label agreement: {correct}/{total} = {correct / total * 100:.1f}%")
    if flagged_total:
        print(
            "Flagged agreement:     "
            f"{flagged_correct}/{flagged_total} = "
            f"{flagged_correct / flagged_total * 100:.1f}%"
        )

    print("\nConfusion matrix (rows=actual, cols=predicted):")
    print(_format_confusion(confusion))

    if disagreements:
        print(f"\nFirst {min(args.show_disagreements, len(disagreements))} disagreements:")
        for d in disagreements[: args.show_disagreements]:
            print(f"  • {d['subject']!r}")
            print(f"      actual={d['actual']!r}  predicted={d['predicted']!r}")
            print(f"      sender: {d['sender']}")
            print(f"      reasoning: {d['reasoning']}")
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
