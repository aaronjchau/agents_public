"""Backtest the Job Apps sublabel classifier against past labeled mail.

Runs classify_sublabel blind on the last N days of sublabeled mail and
prints overall agreement plus a 7x7 confusion matrix. Ship bar: at least
85% top-1 agreement and at least 95% on Offer and Rejection, the two
user-visible calls. Cost: one Sonnet call per email ($0.005 to $0.02).

Usage: uv run python scripts/backtest_job_apps.py --days 30 --max 50
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

from services.job_apps.classifier import classify_sublabel
from services.job_apps.labels import SUBLABELS
from shared.gmail import build_gmail_service, index_headers

SUBLABEL_NAMES: set[str] = set(SUBLABELS)


def actual_sublabel(message: dict[str, Any], id_to_name: dict[str, str]) -> str | None:
    """Return the first Job Apps sublabel currently on a message, or None.

    Sublabels are exclusive in practice, but historical drift means some
    messages carry two; the first match wins.
    """
    for label_id in message.get("labelIds") or []:
        name = id_to_name.get(label_id)
        if name in SUBLABEL_NAMES:
            return name
    return None


def _format_confusion(confusion: dict[str, Counter[str]]) -> str:
    """Render the 7x7 confusion matrix as a fixed-width text table.

    Sublabel names are long, so column headers abbreviate to 6 chars
    while row labels stay full.
    """
    cols = list(SUBLABELS)
    lines: list[str] = []
    header = f"  {'actual\\pred':24s} | " + " | ".join(f"{c[:6]:>6s}" for c in cols)
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for actual in cols:
        row = confusion.get(actual, Counter())
        if not row:
            continue
        cells = " | ".join((f"{row[p]:>6d}" if row[p] else f"{'.':>6s}") for p in cols)
        lines.append(f"  {actual:24s} | {cells}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backtest the new Job Apps sublabel classifier against past mail."
    )
    parser.add_argument("--days", type=int, default=30, help="Days back to scan (default: 30)")
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

    # Filter on the sublabels alone (not the parent "Job Apps" label) so
    # historical mail carrying only a sublabel still surfaces.
    stubs = fetch_labeled_message_stubs(
        service, label_names=SUBLABELS, days=args.days, max_results=args.max
    )
    print(f"Gmail returned {len(stubs)} candidate messages.\n")

    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    disagreements: list[dict[str, str]] = []
    skipped_no_label = 0
    skipped_no_body = 0
    skipped_error = 0

    for i, stub in enumerate(stubs, 1):
        msg = get_message(service, stub["id"])
        actual = actual_sublabel(msg, id_to_name)
        if actual is None:
            skipped_no_label += 1
            continue

        headers = index_headers(msg.get("payload") or {})
        sender = headers.get("From", "")
        subject = headers.get("Subject", "")
        body = extract_body_text(msg.get("payload") or {})

        if not body:
            skipped_no_body += 1
            continue

        try:
            classification = classify_sublabel(
                sender=sender, subject=subject, body_text=body
            ).classification
        except Exception as exc:
            print(
                f"  [{i:3d}/{len(stubs)}] CLASSIFY-ERR {subject[:40]!r}: {exc}",
                file=sys.stderr,
            )
            skipped_error += 1
            continue

        predicted = classification.sublabel
        confusion[actual][predicted] += 1

        match = "OK" if predicted == actual else "DIFF"
        print(
            f"  [{i:3d}/{len(stubs)}] {match:4s} actual={actual!r:<26} "
            f"pred={predicted!r:<26} {subject[:60]!r}"
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

    print()
    print("=" * 80)
    print("RESULTS")
    print("=" * 80)
    print(f"  Considered:        {len(stubs)}")
    print(f"  Skipped (no Sub.): {skipped_no_label}")
    print(f"  Skipped (no body): {skipped_no_body}")
    print(f"  Skipped (errored): {skipped_error}")
    classified_total = sum(sum(row.values()) for row in confusion.values())
    if classified_total == 0:
        print("\nNo messages classified — nothing to report.")
        return 0

    correct_total = sum(confusion[label][label] for label in SUBLABELS)
    print(f"  Classified:        {classified_total}")
    print(
        f"  Top-1 agreement:   {correct_total}/{classified_total} "
        f"({100.0 * correct_total / classified_total:.1f}%)"
    )

    print("\nPER-SUBLABEL accuracy (recall on the actual class):")
    print(f"  {'sublabel':24s} | correct / total | recall")
    print(f"  {'-' * 24}-+-----------------+-------")
    for label in SUBLABELS:
        row = confusion.get(label, Counter())
        total = sum(row.values())
        if total == 0:
            continue
        correct = row[label]
        bar = "<<<" if label in {"Offer", "Rejection"} else ""
        print(
            f"  {label:24s} |   {correct:3d}    /  {total:3d}     | "
            f"{100.0 * correct / total:5.1f}% {bar}"
        )

    print("\nCONFUSION MATRIX")
    print(_format_confusion(confusion))

    if disagreements:
        print(
            f"\nDISAGREEMENTS ({len(disagreements)} total, showing first {args.show_disagreements}):"
        )
        for d in disagreements[: args.show_disagreements]:
            print(f"  - actual={d['actual']!r} pred={d['predicted']!r}")
            print(f"      from: {d['sender']}")
            print(f"      subj: {d['subject']}")
            print(f"      why:  {d['reasoning'][:200]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
