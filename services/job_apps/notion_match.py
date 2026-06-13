"""Match a Job-Apps email to a row in the Notion Job Applications DB.

Two-step cascade that stops at the first hit: deterministic posting-URL
equality, then an LLM choice over candidates with an application date.
Read-only; never creates rows. Design notes: docs/design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from langsmith import traceable

from services.job_apps.types import MatchResult, ParsedEmail
from shared.anthropic_client import get_anthropic_client
from shared.llm_utils import parse_stringified_tool_input, truncate_body
from shared.ntn import page_results, query_all_pages, query_data_source
from shared.settings import get_settings

if TYPE_CHECKING:
    from anthropic import Anthropic
    from anthropic.types import ToolParam, Usage

# ATS hosts whose posting URLs get tracking query params stripped before
# the equality check.
_ATS_HOST_SUFFIXES = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "workable.com",
)

_TRACKING_QUERY_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "gh_src",
        "lever-source",
        "lever-source[]",
        "source",
        "ref",
        "src",
    }
)

_HREF_PATTERN = re.compile(r"https?://[^\s<>\"'\)\]\}]+", re.IGNORECASE)

_DEFAULT_LLM_MODEL = "claude-opus-4-7"
_DEFAULT_LLM_EFFORT = "high"
_DEFAULT_LLM_MAX_TOKENS = 16000

_PROMPT_PATH = Path(__file__).parent / "prompts" / "match_classifier.md"


@dataclass(frozen=True)
class MatchCascadeResult:
    """A MatchResult plus billing data from the LLM step, if it fired.

    usage and model are None when Step 1 or the empty-candidates
    short-circuit resolved the match without calling Anthropic.
    """

    result: MatchResult
    usage: Usage | None
    model: str | None


@traceable(run_type="llm", name="job_apps.match")
def match_email_to_application(
    *,
    parsed_email: ParsedEmail,
    client: Anthropic | None = None,
) -> MatchCascadeResult:
    """Run the cascade and return a MatchCascadeResult.

    urls from the parse node are consulted first; when empty, URLs are
    regex-extracted from the HTML-stripped body_text. usage and model
    are non-None only when the LLM step fired.
    """
    urls = list(parsed_email.urls) if parsed_email.urls else _extract_urls(parsed_email.body_text)
    canonical_urls = _dedupe([_canonicalize_url(u) for u in urls])

    # Step 1: posting URL exact match.
    url_result = _match_by_posting_url(canonical_urls)
    if url_result is not None:
        return MatchCascadeResult(result=url_result, usage=None, model=None)

    # Step 2: LLM match against actually-submitted candidates.
    candidates = _fetch_application_candidates()
    if not candidates:
        return MatchCascadeResult(
            result=MatchResult(status="no_match", candidates=[]),
            usage=None,
            model=None,
        )

    return _llm_match(parsed_email=parsed_email, candidates=candidates, client=client)


# ---------------------------------------------------------------------- URLs


def _extract_urls(html_or_text: str) -> list[str]:
    """Extract URLs from email body. Prefer anchors; fall back to regex."""
    if not html_or_text.strip():
        return []
    urls: list[str] = []
    soup = BeautifulSoup(html_or_text, "html.parser")
    for a in soup.find_all("a"):
        href = a.get("href") if hasattr(a, "get") else None
        if isinstance(href, str) and href.strip().lower().startswith(("http://", "https://")):
            urls.append(href.strip())
    for hit in _HREF_PATTERN.findall(html_or_text):
        if hit not in urls:
            urls.append(hit)
    return _dedupe(urls)


def _canonicalize_url(url: str) -> str:
    """Strip tracking query params for ATS hosts; pass others through."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    host = parsed.netloc.lower()
    if not any(host == h or host.endswith("." + h) for h in _ATS_HOST_SUFFIXES):
        return url
    if not parsed.query:
        return url
    params = parse_qs(parsed.query, keep_blank_values=True)
    kept = {k: v for k, v in params.items() if k not in _TRACKING_QUERY_PARAMS}
    if kept == params:
        return url
    new_query = urlencode(kept, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --------------------------------------------------------------- match step 1


def _match_by_posting_url(urls: list[str]) -> MatchResult | None:
    """Query Job Apps by Posting URL for each URL; None on zero hits."""
    aggregated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for url in urls:
        for row in _query_job_apps_by_url(url):
            row_id = row.get("id")
            if isinstance(row_id, str) and row_id not in seen_ids:
                seen_ids.add(row_id)
                aggregated.append(row)
    if not aggregated:
        return None
    if len(aggregated) == 1:
        return MatchResult(status="matched", notion_row_id=aggregated[0]["id"])
    return MatchResult(status="ambiguous", candidates=aggregated)


def _query_job_apps_by_url(url: str) -> list[dict[str, Any]]:
    body = {
        "filter": {"property": "Posting URL", "url": {"equals": url}},
        "page_size": 25,
    }
    payload = query_data_source(get_settings().notion_job_apps_data_source_id, body)
    return page_results(payload)


# --------------------------------------------------------------- match step 2


def _fetch_application_candidates() -> list[dict[str, Any]]:
    """Pull all submitted Job Apps rows with company names resolved.

    Server-side filter: Application Date is_not_empty. Terminal-status
    rows are included on purpose: follow-up mail about closed
    applications still needs to match its row, and the writer's terminal
    guard makes that safe.
    """
    body = {
        "filter": {"property": "Application Date", "date": {"is_not_empty": True}},
        "page_size": 100,
    }
    rows = query_all_pages(get_settings().notion_job_apps_data_source_id, body)

    company_ids: set[str] = set()
    for row in rows:
        relation = ((row.get("properties") or {}).get("Company") or {}).get("relation") or []
        for r in relation:
            cid = r.get("id")
            if isinstance(cid, str):
                company_ids.add(cid)

    company_name_by_id = _fetch_company_names_by_id(company_ids)

    out: list[dict[str, Any]] = []
    for row in rows:
        props = row.get("properties") or {}
        relation = (props.get("Company") or {}).get("relation") or []
        company_names = [
            company_name_by_id.get(r.get("id"), "?")
            for r in relation
            if isinstance(r.get("id"), str)
        ]
        status_prop = props.get("Status") or {}
        status_obj = status_prop.get("select") or {}
        status_name = status_obj.get("name") if isinstance(status_obj, dict) else None
        posting_url_prop = props.get("Posting URL") or {}
        posting_url = posting_url_prop.get("url") if isinstance(posting_url_prop, dict) else None

        out.append(
            {
                "row_id": row.get("id"),
                "company": ", ".join(company_names) if company_names else "(no company)",
                "role": _row_role_name(row) or "(no role)",
                "status": status_name or "(no status)",
                "posting_url": posting_url,
            }
        )
    return out


def _fetch_company_names_by_id(company_ids: set[str]) -> dict[str, str]:
    """Build a company-id-to-name map for the Companies rows needed.

    Paginates through the entire Companies DB; a single-page fetch
    silently returns "?" for companies on later pages, leaving the LLM
    candidates with no readable company name.
    """
    if not company_ids:
        return {}
    rows = query_all_pages(get_settings().notion_companies_data_source_id, {"page_size": 100})
    out: dict[str, str] = {}
    for row in rows:
        rid = row.get("id")
        if not isinstance(rid, str) or rid not in company_ids:
            continue
        name = _row_role_name(row) or "(unnamed)"
        out[rid] = name
    return out


def _row_role_name(row: dict[str, Any]) -> str | None:
    """Pull the title-property text out of a Notion row.

    Databases name their title property differently (Role Name vs
    Company Name), so this finds the property whose type is title (every
    Notion DB has exactly one) and concatenates its plain_text chunks.
    """
    props = row.get("properties") or {}
    title_prop = next(
        (p for p in props.values() if isinstance(p, dict) and p.get("type") == "title"),
        None,
    )
    if not isinstance(title_prop, dict):
        return None
    title_blocks = title_prop.get("title")
    if not isinstance(title_blocks, list):
        return None
    chunks: list[str] = []
    for block in title_blocks:
        if not isinstance(block, dict):
            continue
        plain = block.get("plain_text")
        if isinstance(plain, str):
            chunks.append(plain)
            continue
        text = block.get("text")
        if isinstance(text, dict):
            content = text.get("content")
            if isinstance(content, str):
                chunks.append(content)
    joined = "".join(chunks).strip()
    return joined or None


# ------------------------------------------------------------------ LLM match


@lru_cache(maxsize=1)
def _load_match_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_match_tool_schema() -> ToolParam:
    return {
        "name": "submit_match",
        "description": (
            "Submit the email-to-row match outcome. Call exactly once with "
            "the chosen outcome, optional notion_row_id, and reasoning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": ["matched", "ambiguous", "no_match"],
                    "description": "matched, ambiguous, or no_match.",
                },
                "notion_row_id": {
                    "type": ["string", "null"],
                    "description": (
                        "Row id of the matched candidate. MUST be one of the "
                        "row_id values shown in the CANDIDATES list. Null when "
                        "outcome is ambiguous or no_match."
                    ),
                },
                "reasoning": {
                    "type": "string",
                    "description": "1-3 sentence plain-prose justification.",
                },
            },
            "required": ["outcome", "notion_row_id", "reasoning"],
        },
    }


def _format_candidates(candidates: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for idx, c in enumerate(candidates, 1):
        url = c.get("posting_url") or "null"
        lines.append(
            f"{idx}. row_id={c['row_id']!r} company={c['company']!r} "
            f"role={c['role']!r} status={c['status']!r} posting_url={url!r}"
        )
    return "\n".join(lines)


def _format_user_payload(
    *,
    parsed_email: ParsedEmail,
    candidates: list[dict[str, Any]],
) -> str:
    return (
        f"CANDIDATES (active applications, {len(candidates)} rows):\n"
        f"{_format_candidates(candidates)}\n\n"
        f"EMAIL:\n"
        f"sender: {parsed_email.sender}\n"
        f"subject: {parsed_email.subject}\n\n"
        f"body_text:\n{truncate_body(parsed_email.body_text)}\n"
    )


def _llm_match(
    *,
    parsed_email: ParsedEmail,
    candidates: list[dict[str, Any]],
    client: Anthropic | None,
) -> MatchCascadeResult:
    api = client or get_anthropic_client()
    system_prompt = _load_match_prompt()
    tool = _build_match_tool_schema()

    valid_row_ids = {c["row_id"] for c in candidates if isinstance(c.get("row_id"), str)}

    response = api.messages.create(
        model=_DEFAULT_LLM_MODEL,
        max_tokens=_DEFAULT_LLM_MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=system_prompt,
        tools=[tool],
        tool_choice={"type": "auto"},
        messages=[
            {
                "role": "user",
                "content": _format_user_payload(parsed_email=parsed_email, candidates=candidates),
            }
        ],
        extra_body={"output_config": {"effort": _DEFAULT_LLM_EFFORT}},
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_match":
            payload: Any = block.input
            if isinstance(payload, str):
                payload = parse_stringified_tool_input(
                    payload, context="match classifier", stop_reason=response.stop_reason
                )
            result = _validate_llm_output(
                cast("dict[str, Any]", payload),
                valid_row_ids=valid_row_ids,
                candidates=candidates,
            )
            return MatchCascadeResult(result=result, usage=response.usage, model=_DEFAULT_LLM_MODEL)

    raise RuntimeError(
        "match classifier response missing submit_match tool_use block; "
        f"stop_reason={response.stop_reason!r}"
    )


def _validate_llm_output(
    payload: dict[str, Any],
    *,
    valid_row_ids: set[str],
    candidates: list[dict[str, Any]],
) -> MatchResult:
    """Map the LLM tool output to a MatchResult with defensive checks.

    A matched outcome must point at a candidate that was sent; an
    unknown id degrades to ambiguous rather than trusting an invented
    match.
    """
    outcome = payload.get("outcome")
    row_id = payload.get("notion_row_id")
    if outcome == "matched":
        if not isinstance(row_id, str) or row_id not in valid_row_ids:
            return MatchResult(status="ambiguous", candidates=candidates)
        return MatchResult(status="matched", notion_row_id=row_id, candidates=candidates)
    if outcome == "ambiguous":
        return MatchResult(status="ambiguous", candidates=candidates)
    return MatchResult(status="no_match", candidates=candidates)


__all__ = [
    "MatchCascadeResult",
    "match_email_to_application",
]
