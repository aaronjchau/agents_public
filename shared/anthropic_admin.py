"""Anthropic Admin API sync: pulls daily cost and usage into anthropic_spend.

Joins the cost_report and usage_report endpoints on (spend_date, model)
and upserts, so daily re-runs replace prior rows.

Design notes: docs/design.md.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from anyio import to_thread
from sqlalchemy.dialects.postgresql import insert as pg_insert

from shared.db import AnthropicSpend, get_session
from shared.settings import get_settings

logger = logging.getLogger(__name__)

ADMIN_API_BASE = "https://api.anthropic.com"
ADMIN_VERSION = "2023-06-01"
_HTTP_TIMEOUT_S = 30.0


def _iso_z(dt: datetime) -> str:
    """Format a datetime as RFC 3339 with an explicit Z suffix, second precision."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paginated_get(
    *,
    url: str,
    headers: dict[str, str],
    params: dict[str, str | list[str]],
) -> list[dict[str, Any]]:
    """Page through an Admin API endpoint, flattening bucket rows.

    Both report endpoints share the same envelope; each flattened row
    carries its bucket's starting_at so downstream code can re-bucket
    by date.
    """
    out: list[dict[str, Any]] = []
    next_page: str | None = None
    while True:
        if next_page:
            params["page"] = next_page
        r = httpx.get(url, headers=headers, params=params, timeout=_HTTP_TIMEOUT_S)
        r.raise_for_status()
        body = r.json()
        for bucket in body.get("data", []):
            for row in bucket.get("results", []):
                out.append({"starting_at": bucket.get("starting_at"), **row})
        if not body.get("has_more"):
            break
        next_page = body.get("next_page")
        if not next_page:
            break
    return out


def fetch_cost_report(
    *, admin_key: str, starting_at: datetime, ending_at: datetime
) -> list[dict[str, Any]]:
    """Fetch daily USD rows, flattened and keyed by bucket start.

    cost_report only accepts group_by of description or workspace_id, not
    model (unlike the usage endpoint), so rows are grouped by description.
    Each row still carries a top-level model field, which _aggregate_rows
    sums by (date, model).
    """
    return _paginated_get(
        url=f"{ADMIN_API_BASE}/v1/organizations/cost_report",
        headers={"x-api-key": admin_key, "anthropic-version": ADMIN_VERSION},
        params={
            "starting_at": _iso_z(starting_at),
            "ending_at": _iso_z(ending_at),
            "bucket_width": "1d",
            "group_by[]": ["description"],
            "limit": "31",
        },
    )


def fetch_usage_report(
    *, admin_key: str, starting_at: datetime, ending_at: datetime
) -> list[dict[str, Any]]:
    """Fetch daily token counts per model, flattened and keyed by bucket start."""
    return _paginated_get(
        url=f"{ADMIN_API_BASE}/v1/organizations/usage_report/messages",
        headers={"x-api-key": admin_key, "anthropic-version": ADMIN_VERSION},
        params={
            "starting_at": _iso_z(starting_at),
            "ending_at": _iso_z(ending_at),
            "bucket_width": "1d",
            "group_by[]": ["model"],
            "limit": "31",
        },
    )


def _parse_bucket_date(starting_at: str | None) -> date | None:
    if not starting_at:
        return None
    try:
        return datetime.fromisoformat(starting_at.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _aggregate_rows(
    cost_rows: list[dict[str, Any]], usage_rows: list[dict[str, Any]]
) -> dict[tuple[date, str], dict[str, Any]]:
    """Merge cost and usage rows into one dict keyed by (spend_date, model).

    Anthropic can return multiple rows per (bucket, model) when the bucket
    is further split (workspace, api key); sum across them. Rows with an
    unparseable starting_at are dropped.
    """
    by_key: dict[tuple[date, str], dict[str, Any]] = {}

    # Anthropic's cost_report returns amount in CENTS despite labeling
    # currency: USD, confirmed empirically: May 15 Sonnet had 71,759 input
    # tokens x $3/M = $0.215 and the API returned amount=21.5277, exactly
    # 100x. Divide by 100 to store dollars.
    _CENTS_PER_USD = Decimal("100")
    for r in cost_rows:
        d = _parse_bucket_date(r.get("starting_at"))
        if d is None:
            continue
        model = r.get("model") or "(unknown)"
        amount = r.get("amount")
        if amount is None:
            continue
        entry = by_key.setdefault(
            (d, model),
            {
                "cost_usd": Decimal("0"),
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_5m": 0,
                "cache_write_1h": 0,
            },
        )
        entry["cost_usd"] = entry["cost_usd"] + (Decimal(str(amount)) / _CENTS_PER_USD)

    for r in usage_rows:
        d = _parse_bucket_date(r.get("starting_at"))
        if d is None:
            continue
        model = r.get("model") or "(unknown)"
        entry = by_key.setdefault(
            (d, model),
            {
                "cost_usd": Decimal("0"),
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_5m": 0,
                "cache_write_1h": 0,
            },
        )
        entry["input_tokens"] += int(r.get("uncached_input_tokens") or 0)
        entry["output_tokens"] += int(r.get("output_tokens") or 0)
        entry["cache_read_tokens"] += int(r.get("cache_read_input_tokens") or 0)
        cache_creation = r.get("cache_creation") or {}
        entry["cache_write_5m"] += int(cache_creation.get("ephemeral_5m_input_tokens") or 0)
        entry["cache_write_1h"] += int(cache_creation.get("ephemeral_1h_input_tokens") or 0)

    return by_key


async def sync_anthropic_spend(*, since: date, until: date) -> int:
    """Fetch Admin API reports for [since, until] and upsert into anthropic_spend.

    Returns the number of rows written. No-ops (returns 0) when
    ANTHROPIC_ADMIN_API_KEY is unset so the daily cron stays green until
    the admin key is provisioned. Both ends of the range are inclusive;
    the API treats ending_at as exclusive, so one day is added before
    calling.
    """
    settings = get_settings()
    admin_key = settings.anthropic_admin_api_key
    if not admin_key:
        logger.warning("ANTHROPIC_ADMIN_API_KEY not set; skipping anthropic_spend sync")
        return 0

    start_dt = datetime.combine(since, datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(until + timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    # The fetchers are blocking httpx calls (30s timeouts); run them in
    # worker threads so they don't stall the event loop.
    cost_rows = await to_thread.run_sync(
        lambda: fetch_cost_report(admin_key=admin_key, starting_at=start_dt, ending_at=end_dt)
    )
    usage_rows = await to_thread.run_sync(
        lambda: fetch_usage_report(admin_key=admin_key, starting_at=start_dt, ending_at=end_dt)
    )

    by_key = _aggregate_rows(cost_rows, usage_rows)
    if not by_key:
        logger.info("anthropic_spend sync %s..%s: 0 rows returned", since, until)
        return 0

    written = 0
    async with get_session() as session:
        for (spend_date, model), payload in by_key.items():
            stmt = pg_insert(AnthropicSpend).values(
                spend_date=spend_date,
                model=model,
                cost_usd=payload["cost_usd"],
                input_tokens=payload["input_tokens"] or None,
                output_tokens=payload["output_tokens"] or None,
                cache_read_tokens=payload["cache_read_tokens"] or None,
                cache_write_5m=payload["cache_write_5m"] or None,
                cache_write_1h=payload["cache_write_1h"] or None,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["spend_date", "model"],
                set_={
                    "cost_usd": stmt.excluded.cost_usd,
                    "input_tokens": stmt.excluded.input_tokens,
                    "output_tokens": stmt.excluded.output_tokens,
                    "cache_read_tokens": stmt.excluded.cache_read_tokens,
                    "cache_write_5m": stmt.excluded.cache_write_5m,
                    "cache_write_1h": stmt.excluded.cache_write_1h,
                    "pulled_at": datetime.now(tz=UTC),
                },
            )
            await session.execute(stmt)
            written += 1
        await session.commit()

    logger.info("anthropic_spend sync %s..%s: %d rows", since, until, written)
    return written
