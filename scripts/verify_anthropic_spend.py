"""Pull actual Anthropic billing from the Admin Usage and Cost API.

Cross-checks the per-call response.usage numbers against what Anthropic's
billing system charged, grouped by model. Needs ANTHROPIC_ADMIN_API_KEY
(minted at console.anthropic.com/settings/admin-keys; separate from
ANTHROPIC_API_KEY, it reads org billing but cannot run inference).

Usage: uv run python scripts/verify_anthropic_spend.py --hours 24
(defaults to the last 24 hours).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

ADMIN_API_BASE = "https://api.anthropic.com"
ADMIN_VERSION = "2023-06-01"


def iso_z(dt: datetime) -> str:
    """Format a datetime as RFC 3339 with an explicit Z suffix, second precision."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_report(
    endpoint: str,
    *,
    admin_key: str,
    starting_at: datetime,
    ending_at: datetime,
) -> list[dict[str, Any]]:
    """Page through an Admin API report endpoint, flattening bucket rows."""
    url = f"{ADMIN_API_BASE}{endpoint}"
    headers = {
        "x-api-key": admin_key,
        "anthropic-version": ADMIN_VERSION,
    }
    params: dict[str, str | list[str]] = {
        "starting_at": iso_z(starting_at),
        "ending_at": iso_z(ending_at),
        "bucket_width": "1d",
        "group_by[]": ["model"],
        "limit": "31",
    }

    out: list[dict[str, Any]] = []
    next_page: str | None = None
    while True:
        if next_page:
            params["page"] = next_page
        r = httpx.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        for bucket in body.get("data", []):
            for row in bucket.get("results", []):
                out.append(
                    {
                        "starting_at": bucket.get("starting_at"),
                        "ending_at": bucket.get("ending_at"),
                        **row,
                    }
                )
        if not body.get("has_more"):
            break
        next_page = body.get("next_page")
        if not next_page:
            break
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="how many hours back to query (default 24)",
    )
    args = parser.parse_args()

    admin_key = os.environ.get("ANTHROPIC_ADMIN_API_KEY", "").strip()
    if not admin_key:
        print(
            "ANTHROPIC_ADMIN_API_KEY not set. See the docstring at the top "
            "of this file for setup steps.",
            file=sys.stderr,
        )
        return 2

    end = datetime.now(tz=UTC)
    start = end - timedelta(hours=args.hours)

    print(f"Querying Admin Usage API: {iso_z(start)} → {iso_z(end)}\n")

    # 1) Authoritative billed amounts from Anthropic.
    try:
        cost_rows = _fetch_report(
            "/v1/organizations/cost_report",
            admin_key=admin_key,
            starting_at=start,
            ending_at=end,
        )
    except httpx.HTTPStatusError as exc:
        print(f"cost_report call failed: {exc} body={exc.response.text}", file=sys.stderr)
        return 1

    by_model_cost: dict[str, float] = {}
    for r in cost_rows:
        model = r.get("model") or "(unknown)"
        # amount is a decimal string in lowest currency units (cents): the
        # documented example is "123.45" in USD = $1.23. Convert to dollars.
        amount = r.get("amount")
        if amount is not None:
            by_model_cost[model] = by_model_cost.get(model, 0.0) + float(amount) / 100.0

    print("=" * 80)
    print("ANTHROPIC-REPORTED SPEND BY MODEL (cost_report)")
    print("=" * 80)
    if not by_model_cost:
        print("(no cost data returned for this window)")
    else:
        for model, amt in sorted(by_model_cost.items(), key=lambda kv: -kv[1]):
            print(f"  {model:<40} ${amt:>8.4f}")
        print(f"  {'TOTAL':<40} ${sum(by_model_cost.values()):>8.4f}")
    print()

    # 2) Token usage breakdown (independent confirmation of the math).
    try:
        usage_rows = _fetch_report(
            "/v1/organizations/usage_report/messages",
            admin_key=admin_key,
            starting_at=start,
            ending_at=end,
        )
    except httpx.HTTPStatusError as exc:
        print(f"usage_report call failed: {exc} body={exc.response.text}", file=sys.stderr)
        return 1

    print("=" * 80)
    print("ANTHROPIC-REPORTED TOKEN USAGE BY MODEL (usage_report/messages)")
    print("=" * 80)
    by_model_tokens: dict[str, dict[str, int]] = {}
    for r in usage_rows:
        model = r.get("model") or "(unknown)"
        agg = by_model_tokens.setdefault(
            model,
            {"uncached_input": 0, "output": 0, "cache_read": 0, "cache_5m_w": 0, "cache_1h_w": 0},
        )
        agg["uncached_input"] += int(r.get("uncached_input_tokens") or 0)
        agg["output"] += int(r.get("output_tokens") or 0)
        agg["cache_read"] += int(r.get("cache_read_input_tokens") or 0)
        cache_creation = r.get("cache_creation") or {}
        agg["cache_5m_w"] += int(cache_creation.get("ephemeral_5m_input_tokens") or 0)
        agg["cache_1h_w"] += int(cache_creation.get("ephemeral_1h_input_tokens") or 0)

    if not by_model_tokens:
        print("(no usage data returned for this window)")
    else:
        for model, agg in sorted(by_model_tokens.items()):
            print(f"  {model}:")
            print(f"    uncached input  {agg['uncached_input']:>12,}")
            print(f"    output (incl. thinking) {agg['output']:>12,}")
            print(f"    cache read      {agg['cache_read']:>12,}")
            print(f"    cache write 5m  {agg['cache_5m_w']:>12,}")
            print(f"    cache write 1h  {agg['cache_1h_w']:>12,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
