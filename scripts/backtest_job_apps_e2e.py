"""End-to-end shadow backtest of the Job Apps pipeline.

Runs classify, match, and extract on historical Job-Apps-labeled mail and
prints actual versus predicted sublabel plus the match and metadata outcomes.
Notion is read-only here (nothing is PATCHed), so the run is safe before
deploy. Cost: about 3 Opus calls per fully-run email, roughly $0.20-$0.40
each; URL-only matches skip the LLM match step.

Usage: uv run python scripts/backtest_job_apps_e2e.py --days 30 --max 15
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import UTC, datetime
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
from services.job_apps.extractor import extract_email_metadata
from services.job_apps.labels import SUBLABELS
from services.job_apps.notion_match import match_email_to_application
from services.job_apps.types import ParsedEmail
from shared.gmail import build_gmail_service, index_headers

SUBLABEL_NAMES: set[str] = set(SUBLABELS)


def actual_sublabel(message: dict[str, Any], id_to_name: dict[str, str]) -> str | None:
    for label_id in message.get("labelIds") or []:
        name = id_to_name.get(label_id)
        if name in SUBLABEL_NAMES:
            return name
    return None


def _summarize_metadata(metadata: Any) -> str:
    """Compact one-line view of the EmailMetadata fields that aren't null."""
    if metadata is None:
        return "(no extraction)"
    fields: list[str] = []
    for k, v in metadata.model_dump().items():
        if v is None:
            continue
        s = str(v)
        if len(s) > 50:
            s = s[:47] + "..."
        fields.append(f"{k}={s}")
    return ", ".join(fields) if fields else "(all null)"


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max", type=int, default=15)
    args = parser.parse_args()

    service = build_gmail_service()
    id_to_name = load_label_id_to_name(service)

    print(f"Pulling Job Apps emails (last {args.days} days, max {args.max})...")
    stubs = fetch_labeled_message_stubs(
        service, label_names=("Job Apps",), days=args.days, max_results=args.max
    )
    print(f"  {len(stubs)} candidate messages.\n")

    sublabel_confusion: dict[str, Counter[str]] = {}
    for s in SUBLABELS:
        sublabel_confusion[s] = Counter()

    match_outcomes: Counter[str] = Counter()
    extraction_calls = 0
    skipped_no_actual = 0
    skipped_no_body = 0
    errored = 0

    print("=" * 110)
    for i, stub in enumerate(stubs, 1):
        msg = get_message(service, stub["id"])
        actual = actual_sublabel(msg, id_to_name)

        headers = index_headers(msg.get("payload") or {})
        sender = headers.get("From", "")
        subject = headers.get("Subject", "")
        body = extract_body_text(msg.get("payload") or {})
        if not body:
            skipped_no_body += 1
            continue

        parsed_email = ParsedEmail(
            sender=sender,
            subject=subject,
            body_text=body,
            urls=[],
            received_at_utc=datetime.now(tz=UTC),
        )

        try:
            classification = classify_sublabel(
                sender=sender, subject=subject, body_text=body
            ).classification
        except Exception as exc:
            print(f"  [{i:>3}] CLASSIFY-ERR {subject[:40]!r}: {exc}", file=sys.stderr)
            errored += 1
            continue

        predicted = classification.sublabel

        # Score the confusion matrix only when there is a label to compare
        # against; some emails predate sublabel application.
        if actual is not None:
            sublabel_confusion[actual][predicted] += 1
        else:
            skipped_no_actual += 1

        # Run match (read-only against Notion).
        try:
            match_result = match_email_to_application(parsed_email=parsed_email).result
        except Exception as exc:
            print(f"  [{i:>3}] MATCH-ERR {subject[:40]!r}: {exc}", file=sys.stderr)
            match_result = None
        match_outcomes[match_result.status if match_result else "errored"] += 1
        match_label = (
            f"{match_result.status}({(match_result.notion_row_id or '-')[:8]})"
            if match_result and match_result.notion_row_id
            else (match_result.status if match_result else "errored")
        )

        # Run extract only on the matched-actionable sublabels (mirrors the
        # graph's gating: extract runs after match-matched).
        metadata = None
        if match_result and match_result.status == "matched":
            try:
                metadata = extract_email_metadata(
                    parsed_email=parsed_email, sublabel=predicted
                ).metadata
                extraction_calls += 1
            except Exception as exc:
                print(f"  [{i:>3}] EXTRACT-ERR {subject[:40]!r}: {exc}", file=sys.stderr)

        agree = "  " if actual is None else ("OK" if predicted == actual else "DIFF")
        print(
            f"[{i:>3}] {agree} actual={actual or '?':<26.26} pred={predicted:<26.26} "
            f"match={match_label:<24.24} {subject[:40]!r}"
        )
        if metadata is not None:
            print(f"        extracted: {_summarize_metadata(metadata)}")
        if predicted != actual and actual is not None:
            print(f"        why: {classification.reasoning[:160]}")

    print()
    print("=" * 110)
    print("SUMMARY")
    print("=" * 110)
    classified_total = sum(sum(row.values()) for row in sublabel_confusion.values())
    print(f"  considered:       {len(stubs)}")
    print(f"  no actual label:  {skipped_no_actual}")
    print(f"  no body:          {skipped_no_body}")
    print(f"  errored:          {errored}")
    print(f"  scored vs actual: {classified_total}")

    if classified_total > 0:
        correct = sum(sublabel_confusion[label][label] for label in SUBLABELS)
        print(
            f"  sublabel agreement: {correct}/{classified_total} "
            f"({100.0 * correct / classified_total:.1f}%)"
        )
        print("\n  per-sublabel recall:")
        for label in SUBLABELS:
            row = sublabel_confusion.get(label, Counter())
            total = sum(row.values())
            if total == 0:
                continue
            ok = row[label]
            bar = " <<<" if label in {"Offer", "Rejection"} else ""
            print(f"    {label:24s}  {ok:>3d}/{total:<3d}  ({100.0 * ok / total:5.1f}%){bar}")

    print(f"\n  match outcomes: {dict(match_outcomes)}")
    print(f"  extractions run (matched only): {extraction_calls}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
