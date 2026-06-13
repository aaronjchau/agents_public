"""HTML to ParsedEmail extractor for news-brief sources.

Replaces every kept anchor with a "[N] anchor_text" marker so plain_text and
links line up by id; footer noise and pixel hosts are dropped and known
click-tracker wrappers are decoded (each recipe is documented at its decode
site). When a new wrapper or pixel host shows up in the wild, add it to
_unwrap_tracking or _PIXEL_HOST_PATH_PREFIXES. Design notes: docs/design.md.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup, NavigableString, Tag

from services.news_brief.types import Link, ParsedEmail

if TYPE_CHECKING:
    from datetime import datetime

_FOOTER_KEYWORDS = (
    "unsubscribe",
    "view in browser",
    "view this email in your browser",
    "view online",
    "view in your browser",
    "manage preferences",
    "manage your preferences",
    "manage subscriptions",
    "email preferences",
    "update preferences",
    "privacy policy",
    "terms of service",
    "share on twitter",
    "share on facebook",
    "share on linkedin",
    "share via email",
    "tweet",
    "forward to a friend",
)

_TAGS_TO_DROP = ("script", "style", "head", "meta", "link", "title")
_PARAGRAPH_LIKE_TAGS = (
    "p",
    "div",
    "section",
    "article",
    "br",
    "tr",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
)

# Hosts/path-prefixes whose links never point to an article worth citing.
# Match is host-suffix plus an optional path-prefix check.
_PIXEL_HOST_PATH_PREFIXES: tuple[tuple[str, str], ...] = (
    ("sli.bloomberg.com", ""),
    ("links.message.bloomberg.com", "/s/"),
    ("link.theverge.com", "/img/"),
    ("link.theverge.com", "/view/"),
    ("link.theverge.com", "/oc/"),
    ("link.theverge.com", "/manage/"),
    ("eotrx.substackcdn.com", ""),
)

# Subscriber-tracking query params to strip from any URL we keep.
_TRACKING_QUERY_PARAMS = {"cmpid", "ueid", "bxid"}


def parse_email_html(
    raw_html: str,
    *,
    message_id: str,
    sender: str,
    subject: str,
    received_at: datetime,
) -> ParsedEmail:
    """Convert raw email HTML into a ParsedEmail.

    Each kept anchor becomes a "[N] TEXT" inline marker with a matching
    Link(id=N, url=URL, anchor_text=TEXT) appended to links (1-indexed, in
    document order). Footer noise and tracking pixels are stripped first.
    """
    soup = BeautifulSoup(raw_html, "html.parser")

    for name in _TAGS_TO_DROP:
        for tag in soup.find_all(name):
            tag.decompose()

    for img in soup.find_all("img"):
        if _is_tracking_pixel(img):
            img.decompose()

    links: list[Link] = []
    next_id = 1
    for a in list(soup.find_all("a")):
        if not isinstance(a, Tag):
            continue
        href = a.get("href")
        anchor_text = a.get_text(separator=" ", strip=True)
        if not isinstance(href, str) or not href.strip() or not anchor_text:
            a.unwrap()
            continue
        if _is_footer_link(href, anchor_text) or _is_pixel_url(href):
            a.decompose()
            continue
        url = _unwrap_tracking(href)
        link = Link(id=next_id, url=url, anchor_text=anchor_text)
        links.append(link)
        next_id += 1
        # Replace the anchor with a "[N] anchor_text" text node, preserving
        # the marker-immediately-before-text contract documented in types.py.
        a.replace_with(NavigableString(f"[{link.id}] {anchor_text}"))

    # Insert newlines around block-level tags so get_text doesn't smush
    # paragraphs together. Done after link substitution so the markers
    # survive the linebreak insertion.
    for tag_name in _PARAGRAPH_LIKE_TAGS:
        for tag in soup.find_all(tag_name):
            if isinstance(tag, Tag):
                tag.insert_before(NavigableString("\n"))
                tag.insert_after(NavigableString("\n"))

    raw_text = soup.get_text(separator=" ")
    plain_text = _normalize_whitespace(raw_text)

    return ParsedEmail(
        message_id=message_id,
        sender=sender,
        subject=subject,
        received_at=received_at,
        plain_text=plain_text,
        links=links,
    )


def _is_tracking_pixel(img: Tag) -> bool:
    """Return True for 1x1 (or zero-sized) images, the tracking-pixel shape."""

    def _to_int(value: object) -> int | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip().rstrip("px").strip()
        try:
            return int(float(cleaned))
        except ValueError:
            return None

    # Real newsletter HTML occasionally produces tags whose attrs is None
    # (malformed inline closes); bs4's Tag.get would raise AttributeError.
    if not getattr(img, "attrs", None):
        return False
    width = _to_int(img.get("width"))
    height = _to_int(img.get("height"))
    if width is not None and width <= 1:
        return True
    if height is not None and height <= 1:  # noqa: SIM103
        return True
    return False


def _is_footer_link(href: str, anchor_text: str) -> bool:
    text_lower = anchor_text.lower().strip()
    if any(keyword in text_lower for keyword in _FOOTER_KEYWORDS):
        return True
    href_lower = href.lower()
    return "unsubscribe" in href_lower or "/preferences" in href_lower


def _is_pixel_url(href: str) -> bool:
    """Return True if href points at a known pixel/beacon host with no article."""
    try:
        parsed = urlparse(href)
    except ValueError:
        return False
    host = parsed.netloc.lower()
    path = parsed.path
    return any(
        (host == known_host or host.endswith("." + known_host))
        and (path_prefix == "" or path.startswith(path_prefix))
        for known_host, path_prefix in _PIXEL_HOST_PATH_PREFIXES
    )


def _unwrap_tracking(href: str) -> str:
    """Strip known click-tracker wrappers; fall back to the original href.

    Server-side opaque wrappers fall through unchanged: emitting the wrapper
    URL is strictly better than dropping the link, and the curator is told
    NYT routes through a dual-source lookup anyway.
    """
    try:
        parsed = urlparse(href)
    except ValueError:
        return href

    host = parsed.netloc.lower()
    path = parsed.path

    # The Verge: link.theverge.com/click/<campaign>/<base64>/<recipient>
    if host == "link.theverge.com" and path.startswith("/click/"):
        unwrapped = _decode_verge_click(path)
        if unwrapped:
            return _strip_tracking_params(unwrapped)

    # Substack: substack.com/redirect/2/<payload>.<signature>
    if host == "substack.com" and path.startswith("/redirect/2/"):
        unwrapped = _decode_substack_jwt(path)
        if unwrapped:
            return _strip_tracking_params(unwrapped)

    # Hacker Newsletter (Buttondown): /c/<base64> where the decoded payload
    # is <recipient_uuid>|<campaign_uuid>|<real_url>|email. Keyed on path
    # prefix rather than exact host so future Buttondown domain rotations
    # (e.g. buttondown-0006.com) keep working.
    if path.startswith("/c/") and ("buttondown" in host):
        unwrapped = _decode_buttondown_pipe(path)
        if unwrapped:
            return _strip_tracking_params(unwrapped)

    # The Information AM briefing wraps every link via Customer.io:
    # e.customeriomail.com/e/c/<base64 of {"href": "<real>", ...}>
    if host == "e.customeriomail.com" and path.startswith("/e/c/"):
        unwrapped = _decode_customerio_json(path)
        if unwrapped:
            return _strip_tracking_params(unwrapped)

    # Direct publisher URLs tagged with utm_*/cmpid (Bloomberg articles,
    # The Information AM direct links): just drop the tracking query params.
    if host.endswith("bloomberg.com") or host.endswith("theinformation.com"):
        return _strip_tracking_params(href)

    # All other wrappers (NYT nl.nytimes.com, urlNNNN.theinformation.com)
    # are server-side opaque; kept as-is, see this function's docstring.
    return href


def _decode_verge_click(path: str) -> str | None:
    """Verge: /click/<campaign>/<base64_real_url>/<recipient>."""
    parts = path.split("/")
    # ["", "click", "<campaign>", "<base64>", "<recipient>"]
    if len(parts) < 4:
        return None
    encoded = parts[3]
    decoded = _b64_decode_text(encoded)
    if decoded is None or not decoded.startswith(("http://", "https://")):
        return None
    return decoded


def _decode_substack_jwt(path: str) -> str | None:
    """Substack: /redirect/2/<payload>.<sig>; extract the e claim.

    The corpus uses a 2-segment payload.signature token, not a standard
    3-segment JWT, so the payload is segment 0. Segment 1 is tried as a
    fallback in case a future Substack rotation switches shape.
    """
    parts = path.split("/")
    # ["", "redirect", "2", "<token>"]
    if len(parts) < 4:
        return None
    token = parts[3]
    segments = token.split(".")
    for candidate in segments[:2]:
        payload_text = _b64_decode_text(candidate)
        if payload_text is None:
            continue
        try:
            data = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        real = data.get("e") if isinstance(data, dict) else None
        if isinstance(real, str) and real.startswith(("http://", "https://")):
            return real
    return None


def _decode_buttondown_pipe(path: str) -> str | None:
    """Buttondown: /c/<base64 of <uuid>|<uuid>|<real_url>|email>."""
    parts = path.split("/")
    # ["", "c", "<base64>"]
    if len(parts) < 3:
        return None
    decoded = _b64_decode_text(parts[2])
    if decoded is None:
        return None
    fields = decoded.split("|")
    if len(fields) < 3:
        return None
    real = fields[2]
    if not real.startswith(("http://", "https://")):
        return None
    return real


def _decode_customerio_json(path: str) -> str | None:
    """Customer.io: /e/c/<base64 of {"href": "<real_url>", ...}>."""
    parts = path.split("/")
    # ["", "e", "c", "<base64>"]
    if len(parts) < 4:
        return None
    decoded = _b64_decode_text(parts[3])
    if decoded is None:
        return None
    try:
        data = json.loads(decoded)
    except json.JSONDecodeError:
        return None
    real = data.get("href") if isinstance(data, dict) else None
    if not isinstance(real, str) or not real.startswith(("http://", "https://")):
        return None
    return real


def _b64_decode_text(encoded: str) -> str | None:
    """Decode URL-safe base64 with auto-padding; return UTF-8 text or None."""
    if not encoded:
        return None
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _strip_tracking_params(url: str) -> str:
    """Remove utm_* and cmpid/ueid/bxid query params; preserve everything else."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.query:
        return url
    params = parse_qs(parsed.query, keep_blank_values=True)
    kept = {
        k: v
        for k, v in params.items()
        if not k.startswith("utm_") and k not in _TRACKING_QUERY_PARAMS
    }
    if kept == params:
        return url
    new_query = urlencode(kept, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


_WHITESPACE_RUN = re.compile(r"[ \t]+")
_BLANK_LINE_RUN = re.compile(r"\n\s*\n\s*(\n\s*)+")


def _normalize_whitespace(text: str) -> str:
    """Collapse intra-line whitespace; allow at most one blank line gap."""
    lines = [_WHITESPACE_RUN.sub(" ", line).strip() for line in text.splitlines()]
    joined = "\n".join(lines)
    joined = _BLANK_LINE_RUN.sub("\n\n", joined)
    return joined.strip()
