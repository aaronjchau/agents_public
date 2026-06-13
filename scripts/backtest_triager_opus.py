"""One-off comparison: Opus extended thinking versus the Sonnet baseline.

Runs the production classifier prompt and tool schema with the chosen
model and thinking mode, capturing per-call token usage from
response.usage so cost analysis is real, not estimated. The thinking
block's text is ignored for classification, but its token count feeds
the cost report.

Usage:
    uv run python scripts/backtest_triager_opus.py --max 10 --thinking-budget 32000
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from typing import Any, cast

# Importing _backtest_common bridges NOTION_API_TOKEN to NOTION_TOKEN
# before anything below instantiates Settings.
from _backtest_common import (
    extract_body_text,
    fetch_labeled_message_stubs,
    get_message,
    load_label_id_to_name,
)
from anthropic import Anthropic

# Deliberately reuses the classifier's private prompt/schema internals so the
# comparison runs the exact production prompt; parity over the privacy convention.
from services.triager.classifier import (
    _USER_PREAMBLE,
    _build_tool_schema,
    _format_user_payload,
    _load_system_prompt,
)
from services.triager.labels import FLAGGED_LABEL, PRIMARY_LABELS
from services.triager.types import Classification
from shared.gmail import build_gmail_service, index_headers
from shared.llm_utils import parse_stringified_tool_input
from shared.settings import get_settings

OPUS_MODEL = "claude-opus-4-7"
OPUS_46_MODEL = "claude-opus-4-6"
SONNET_MODEL = "claude-sonnet-4-6"

# Per-million-token prices verified against Anthropic's published pricing page
# (claude.com/pricing) on 2026-05-05. Anthropic reduced Opus pricing for the
# 4.5+ generation: Opus 4.7 / 4.6 / 4.5 are $5 / $25 input/output, NOT the
# $15 / $75 that older Opus 4.0 / 4.1 charged. Cache rates follow the
# standard 1.25x (5min write) / 0.1x (read) multipliers off base input.
# cache_write here uses the 5-min rate; the production classifier opts
# into 1h cache (2x base input) but this backtest script uses 5-min cache.
_OPUS_45PLUS_PRICES = {
    "input": 5.0 / 1_000_000,
    "output": 25.0 / 1_000_000,
    "cache_read": 0.50 / 1_000_000,
    "cache_write": 6.25 / 1_000_000,
}
PRICES = {
    OPUS_MODEL: _OPUS_45PLUS_PRICES,
    OPUS_46_MODEL: _OPUS_45PLUS_PRICES,
    SONNET_MODEL: {
        "input": 3.0 / 1_000_000,
        "output": 15.0 / 1_000_000,
        "cache_read": 0.30 / 1_000_000,
        "cache_write": 3.75 / 1_000_000,
    },
}

PRIMARY_LABEL_NAMES: set[str] = set(PRIMARY_LABELS)


def classify_with_thinking(
    *,
    client: Anthropic,
    model: str,
    sender: str,
    subject: str,
    body_text: str,
    thinking_budget: int,
    effort: str,
) -> tuple[Classification, dict[str, int]]:
    """Run a single classification with extended thinking, switching API by model.

    Opus uses the adaptive API (thinking type adaptive, with
    output_config.effort controlling depth); Sonnet uses the legacy
    fixed-budget API. Both drop the forced tool_choice because extended
    thinking is incompatible with forcing; auto with the single registered
    tool still yields structured output. Returns (Classification,
    usage_dict) where usage_dict has keys input_tokens, output_tokens,
    cache_read, and cache_write.
    """
    system_prompt = _load_system_prompt()
    tool = _build_tool_schema()

    # Both Opus 4.6 and 4.7 use the adaptive thinking API; the legacy
    # enabled shape is deprecated for these models. Sonnet 4.6 still uses
    # legacy.
    is_opus = model in (OPUS_MODEL, OPUS_46_MODEL)
    if is_opus:
        thinking_arg: dict[str, Any] = {"type": "adaptive"}
        extra_body: dict[str, Any] | None = {"output_config": {"effort": effort}}
    else:
        thinking_arg = {"type": "enabled", "budget_tokens": thinking_budget}
        extra_body = None

    common_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": thinking_budget + 1024,
        "thinking": thinking_arg,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "tools": [tool],
        "tool_choice": {"type": "auto"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": _USER_PREAMBLE,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": _format_user_payload(
                            sender=sender, subject=subject, body_text=body_text
                        ),
                    },
                ],
            }
        ],
    }
    if extra_body is not None:
        common_kwargs["extra_body"] = extra_body

    # Streaming covers both: Opus xhigh can exceed the 10-min non-streaming
    # timeout, and there's no downside to streaming for Sonnet either.
    with client.messages.stream(**common_kwargs) as s:
        response = s.get_final_message()

    classification: Classification | None = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_classification":
            payload: Any = block.input
            if isinstance(payload, str):
                payload = parse_stringified_tool_input(
                    payload, context="classifier", stop_reason=response.stop_reason
                )
            classification = Classification.model_validate(cast("dict[str, Any]", payload))
            break

    if classification is None:
        raise RuntimeError(
            f"{model} response missing submit_classification tool_use; "
            f"stop_reason={response.stop_reason!r}"
        )

    usage = response.usage
    usage_dict = {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }
    return classification, usage_dict


def cost_of(model: str, usage: dict[str, int]) -> float:
    """Compute USD cost for a single classification given token usage."""
    p = PRICES[model]
    return (
        usage["input_tokens"] * p["input"]
        + usage["output_tokens"] * p["output"]
        + usage["cache_read"] * p["cache_read"]
        + usage["cache_write"] * p["cache_write"]
    )


def actual_primary_label(message: dict[str, Any], id_to_name: dict[str, str]) -> str | None:
    for label_id in message.get("labelIds") or []:
        name = id_to_name.get(label_id)
        if name in PRIMARY_LABEL_NAMES:
            return name
    return None


def actual_flagged(message: dict[str, Any], id_to_name: dict[str, str]) -> bool:
    return any(
        id_to_name.get(label_id) == FLAGGED_LABEL for label_id in message.get("labelIds") or []
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--days", type=int, default=14, help="days back to scan (default 14)")
    parser.add_argument("--max", type=int, default=10, help="max messages (default 10)")
    parser.add_argument(
        "--model",
        type=str,
        default=OPUS_MODEL,
        help=f"model: {OPUS_MODEL} (adaptive thinking) or {SONNET_MODEL} (legacy thinking)",
    )
    parser.add_argument(
        "--thinking-budget",
        type=int,
        default=8000,
        help="legacy thinking budget tokens (Sonnet only; default 8000)",
    )
    parser.add_argument(
        "--effort",
        type=str,
        default="high",
        help="adaptive effort level (Opus only): low, medium, high, xhigh (default high)",
    )
    args = parser.parse_args()

    settings = get_settings()
    client = Anthropic(api_key=settings.anthropic_api_key)
    service = build_gmail_service()
    id_to_name = load_label_id_to_name(service)

    if args.model in (OPUS_MODEL, OPUS_46_MODEL):
        mode_desc = f"effort={args.effort} (adaptive)"
    else:
        mode_desc = f"thinking_budget={args.thinking_budget} (legacy)"
    print(
        f"Backtesting {args.model} with {mode_desc} "
        f"on up to {args.max} messages from the last {args.days} days..."
    )
    stubs = fetch_labeled_message_stubs(
        service, label_names=PRIMARY_LABELS, days=args.days, max_results=args.max
    )
    print(f"Gmail returned {len(stubs)} candidates.\n")

    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    flagged_correct = 0
    flagged_total = 0
    disagreements: list[dict[str, Any]] = []
    skipped = 0

    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0
    total_cost = 0.0
    total_latency = 0.0

    for i, stub in enumerate(stubs, 1):
        msg = get_message(service, stub["id"])
        actual = actual_primary_label(msg, id_to_name)
        if actual is None:
            skipped += 1
            continue

        actual_flag = actual_flagged(msg, id_to_name)
        headers = index_headers(msg.get("payload") or {})
        sender = headers.get("From", "")
        subject = headers.get("Subject", "")
        body = extract_body_text(msg.get("payload") or {})
        if not body:
            skipped += 1
            continue

        t0 = time.perf_counter()
        try:
            classification, usage = classify_with_thinking(
                client=client,
                model=args.model,
                sender=sender,
                subject=subject,
                body_text=body,
                thinking_budget=args.thinking_budget,
                effort=args.effort,
            )
        except Exception as exc:
            print(f"  [{i:3d}/{len(stubs)}] ERROR  {subject[:40]!r}: {exc}", file=sys.stderr)
            skipped += 1
            continue
        dt = time.perf_counter() - t0
        total_latency += dt

        cost = cost_of(args.model, usage)
        total_input += usage["input_tokens"]
        total_output += usage["output_tokens"]
        total_cache_read += usage["cache_read"]
        total_cache_write += usage["cache_write"]
        total_cost += cost

        predicted = classification.primary_label
        confusion[actual][predicted] += 1
        if classification.flagged == actual_flag:
            flagged_correct += 1
        flagged_total += 1

        match = "OK" if predicted == actual else "DIFF"
        flag_match = "F" if actual_flag == classification.flagged else "f"
        print(
            f"  [{i:3d}/{len(stubs)}] {match:4s} {flag_match} "
            f"actual={actual!r:<14} pred={predicted!r:<14} "
            f"in={usage['input_tokens']:5d} out={usage['output_tokens']:5d} "
            f"cache_r={usage['cache_read']:5d} cache_w={usage['cache_write']:5d} "
            f"${cost:.4f} {dt:5.1f}s  {subject[:40]!r}"
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

    print("\n" + "=" * 80)
    print(f"{args.model} ({mode_desc}) BACKTEST RESULTS")
    print("=" * 80)
    if total:
        print(f"Top-1 label agreement: {correct}/{total} = {correct / total * 100:.1f}%")
    if flagged_total:
        print(
            "Flagged agreement:     "
            f"{flagged_correct}/{flagged_total} = "
            f"{flagged_correct / flagged_total * 100:.1f}%"
        )
    print(f"Skipped (no label / no body / error): {skipped}")
    print()
    print("Token usage (totals):")
    print(f"  input          {total_input:>9,}")
    print(f"  output (incl. thinking) {total_output:>9,}")
    print(f"  cache_read     {total_cache_read:>9,}")
    print(f"  cache_write    {total_cache_write:>9,}")
    print()
    print(f"Total cost:       ${total_cost:.4f}")
    if total:
        print(f"Avg cost / email: ${total_cost / total:.4f}")
        print(f"Avg latency:      {total_latency / total:.1f}s")

    if disagreements:
        print(f"\nDisagreements ({len(disagreements)}):")
        for d in disagreements:
            print(f"  • {d['subject']!r}  actual={d['actual']!r}  pred={d['predicted']!r}")
            print(f"    sender: {d['sender']}")
            print(f"    why: {d['reasoning']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
