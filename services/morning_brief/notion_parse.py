"""Pure helpers to pull values out of Notion property JSON.

Everything returns None or empty on a missing or malformed property so a
renamed or absent column degrades the brief instead of raising. The title
is found by type because every database names its title differently.
"""

from __future__ import annotations

from datetime import date
from typing import Any


def title_text(row: dict[str, Any]) -> str:
    """Concatenate the title property's plain text, found by type."""
    props = row.get("properties") or {}
    title_prop = next(
        (p for p in props.values() if isinstance(p, dict) and p.get("type") == "title"),
        None,
    )
    blocks = title_prop.get("title") if isinstance(title_prop, dict) else None
    return _rich_text_chunks(blocks)


def rich_text(props: dict[str, Any], name: str) -> str:
    prop = props.get(name) or {}
    blocks = prop.get("rich_text") if isinstance(prop, dict) else None
    return _rich_text_chunks(blocks)


def number(props: dict[str, Any], name: str) -> float | None:
    prop = props.get(name) or {}
    value = prop.get("number") if isinstance(prop, dict) else None
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def status_name(props: dict[str, Any], name: str) -> str | None:
    return _named_option(props, name, "status")


def select_name(props: dict[str, Any], name: str) -> str | None:
    return _named_option(props, name, "select")


def multi_select(props: dict[str, Any], name: str) -> tuple[str, ...]:
    prop = props.get(name) or {}
    raw = prop.get("multi_select") if isinstance(prop, dict) else None
    return _option_names(raw)


def first_multi_select(row: dict[str, Any]) -> tuple[str, ...]:
    """Return the first multi_select property's values, regardless of name.

    Used where the property name isn't pinned (e.g. the LeetCode topics
    column) so categories surface without hardcoding a possibly-stale name.
    """
    props = row.get("properties") or {}
    for prop in props.values():
        if isinstance(prop, dict) and prop.get("type") == "multi_select":
            return _option_names(prop.get("multi_select"))
    return ()


def relation_ids(props: dict[str, Any], name: str) -> tuple[str, ...]:
    prop = props.get(name) or {}
    raw = prop.get("relation") if isinstance(prop, dict) else None
    if not isinstance(raw, list):
        return ()
    return tuple(r["id"] for r in raw if isinstance(r, dict) and isinstance(r.get("id"), str))


def date_start(props: dict[str, Any], name: str) -> date | None:
    return _date_part(props, name)[0]


def date_range(props: dict[str, Any], name: str) -> tuple[date | None, date | None]:
    return _date_part(props, name)


def parse_iso_date(value: Any) -> date | None:
    """Parse a Notion ISO date/datetime string to a date (date part only)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _date_part(props: dict[str, Any], name: str) -> tuple[date | None, date | None]:
    prop = props.get(name) or {}
    obj = prop.get("date") if isinstance(prop, dict) else None
    if not isinstance(obj, dict):
        return (None, None)
    return (parse_iso_date(obj.get("start")), parse_iso_date(obj.get("end")))


def _named_option(props: dict[str, Any], name: str, key: str) -> str | None:
    prop = props.get(name) or {}
    obj = prop.get(key) if isinstance(prop, dict) else None
    value = obj.get("name") if isinstance(obj, dict) else None
    return value if isinstance(value, str) else None


def _option_names(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(o["name"] for o in raw if isinstance(o, dict) and isinstance(o.get("name"), str))


def _rich_text_chunks(blocks: Any) -> str:
    if not isinstance(blocks, list):
        return ""
    chunks: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        plain = block.get("plain_text")
        if isinstance(plain, str):
            chunks.append(plain)
            continue
        text = block.get("text")
        if isinstance(text, dict) and isinstance(text.get("content"), str):
            chunks.append(text["content"])
    return "".join(chunks)
