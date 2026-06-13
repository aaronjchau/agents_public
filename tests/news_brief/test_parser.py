"""Tests for the news-brief HTML parser.

Synthetic-HTML unit tests pin marker insertion, footer stripping, and
tracking-wrapper unwrapping. Real-fixture tests run only when a local
sample directory exists (NEWS_BRIEF_SAMPLE_DIR) and are skipped in CI.
"""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from services.news_brief.parser import parse_email_html

if TYPE_CHECKING:
    from services.news_brief.types import ParsedEmail

SAMPLE_DIR = Path(os.environ.get("NEWS_BRIEF_SAMPLE_DIR", "/tmp/news-brief-samples"))
_RECEIVED = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)


def _parse(html: str) -> ParsedEmail:
    return parse_email_html(
        html,
        message_id="<test@example.com>",
        sender="test",
        subject="Test subject",
        received_at=_RECEIVED,
    )


def test_anchor_becomes_marker_and_link() -> None:
    html = '<p>Apple announced <a href="https://example.com/m4">new MacBooks</a> today.</p>'
    parsed = _parse(html)

    assert "[1] new MacBooks" in parsed.plain_text
    assert len(parsed.links) == 1
    link = parsed.links[0]
    assert link.id == 1
    assert link.url == "https://example.com/m4"
    assert link.anchor_text == "new MacBooks"


def test_link_ids_are_dense_and_in_document_order() -> None:
    html = """
    <p><a href="https://a.example/1">first</a> then
    <a href="https://b.example/2">second</a> then
    <a href="https://c.example/3">third</a>.</p>
    """
    parsed = _parse(html)

    assert [link.id for link in parsed.links] == [1, 2, 3]
    assert [link.url for link in parsed.links] == [
        "https://a.example/1",
        "https://b.example/2",
        "https://c.example/3",
    ]
    assert parsed.plain_text.find("[1]") < parsed.plain_text.find("[2]")
    assert parsed.plain_text.find("[2]") < parsed.plain_text.find("[3]")


def test_footer_unsubscribe_link_is_dropped() -> None:
    html = """
    <p>Real <a href="https://example.com/story">article</a></p>
    <p><a href="https://example.com/u/123">Unsubscribe</a></p>
    """
    parsed = _parse(html)

    assert len(parsed.links) == 1
    assert parsed.links[0].anchor_text == "article"
    assert "Unsubscribe" not in parsed.plain_text


def test_view_in_browser_link_is_dropped() -> None:
    html = """
    <a href="https://example.com/web/123">View in browser</a>
    <p>Body <a href="https://example.com/story">here</a>.</p>
    """
    parsed = _parse(html)

    anchor_texts = [link.anchor_text for link in parsed.links]
    assert "here" in anchor_texts
    assert "View in browser" not in anchor_texts


def test_tracking_pixel_image_is_dropped() -> None:
    html = '<img src="https://t.example/pixel.gif" width="1" height="1" alt="">'
    parsed = _parse(html)
    assert "pixel" not in parsed.plain_text.lower()


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode()


def test_verge_base64_wrapper_is_unwrapped() -> None:
    real = "https://www.theverge.com/ai/123/elon-altman-trial"
    encoded = _b64url(real)
    wrapped = f"https://link.theverge.com/click/12345678.90123/{encoded}/0123456789abcdef0123456789abcdef0"
    html = f'<p><a href="{wrapped}">Elon Altman trial</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == real


def test_verge_wrapper_strips_ueid_and_bxid() -> None:
    real_with_tracking = (
        "https://www.theverge.com/ai/123/altman-trial?ueid=0123456789abcdef&bxid=fedcba9876543210"
    )
    encoded = _b64url(real_with_tracking)
    wrapped = f"https://link.theverge.com/click/12345678.90123/{encoded}/abc"
    html = f'<p><a href="{wrapped}">altman trial</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == "https://www.theverge.com/ai/123/altman-trial"


def test_substack_two_segment_token_is_unwrapped() -> None:
    # Real Substack tokens in the 2026 corpus are payload.signature, not a
    # 3-segment JWT; the first segment is the one that decodes.
    real = "https://newsletter.pragmaticengineer.com/p/ubuntu-and-ai"
    payload = json.dumps({"e": real, "p": 1957, "s": 458, "iss": "pub-0"})
    body = _b64url(payload)
    sig = _b64url("fake-signature-bytes")
    token = f"{body}.{sig}"
    wrapped = f"https://substack.com/redirect/2/{token}"
    html = f'<p><a href="{wrapped}">Ubuntu and AI</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == real


def test_substack_three_segment_jwt_also_unwraps() -> None:
    # Defensive: tolerate a future rotation back to a standard 3-segment JWT.
    real = "https://newsletter.pragmaticengineer.com/p/three-segment"
    header = _b64url('{"alg":"HS256","typ":"JWT"}')
    body = _b64url(json.dumps({"e": real}))
    sig = _b64url("sig")
    wrapped = f"https://substack.com/redirect/2/{header}.{body}.{sig}"
    html = f'<p><a href="{wrapped}">three segment</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == real


def test_customerio_wrapper_is_unwrapped() -> None:
    real = "https://www.theinformation.com/briefings/anthropic-funding"
    payload = json.dumps({"email_id": "abc==", "href": real, "link_id": 12345})
    encoded = _b64url(payload)
    wrapped = f"https://e.customeriomail.com/e/c/{encoded}/0a1b2c3d4"
    html = f'<p><a href="{wrapped}">Anthropic funding</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == real


def test_customerio_wrapper_strips_utm_after_decode() -> None:
    payload = json.dumps(
        {
            "href": "https://www.bloomberg.com/news/articles/2026/foo"
            "?utm_campaign=ti-am&utm_medium=email&keep=ok",
        }
    )
    encoded = _b64url(payload)
    wrapped = f"https://e.customeriomail.com/e/c/{encoded}"
    html = f'<p><a href="{wrapped}">foo</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == "https://www.bloomberg.com/news/articles/2026/foo?keep=ok"


def test_verge_oc_and_manage_paths_are_dropped() -> None:
    # /oc/... is a one-click unsubscribe link and /manage/... is the
    # preferences center; neither is an article.
    html = """
    <a href="https://link.theverge.com/oc/fedcba9876543210/0a1b2c3d">unsubscribe</a>
    <a href="https://link.theverge.com/manage/6xd/oc?email=x@y.com">manage</a>
    <p>Real <a href="https://www.theverge.com/story/123">article</a></p>
    """
    parsed = _parse(html)
    assert len(parsed.links) == 1
    assert parsed.links[0].url == "https://www.theverge.com/story/123"


def test_buttondown_pipe_base64_wrapper_is_unwrapped() -> None:
    real = "https://buttondown.com/hacker-newsletter/archive/791/"
    payload = (
        f"00000000-1111-4222-8333-444444444444|55555555-6666-4777-8888-999999999999|{real}|email"
    )
    encoded = _b64url(payload)
    wrapped = f"https://buttondown-0005.com/c/{encoded}"
    html = f'<p><a href="{wrapped}">issue 791</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == real


def test_nyt_2026_wrapper_falls_through_unchanged() -> None:
    # NYT's 2026 wrapper is server-side opaque; kept as-is so downstream
    # can still feed it to the LLM (which dual-sources NYT articles anyway).
    wrapped = "https://nl.nytimes.com/f/a/AbCdEfGhIjKlMnOpQrStUv~~/AAAAAAA~/aBcDeFgHiJkL"
    html = f'<p><a href="{wrapped}">NYT story</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == wrapped


def test_bloomberg_pixel_host_is_dropped() -> None:
    html = """
    <p>Real <a href="https://www.bloomberg.com/news/articles/2026-04-30/fed">article</a></p>
    <p><a href="https://sli.bloomberg.com/click?s=1149">pixel</a></p>
    <p><a href="https://links.message.bloomberg.com/s/c/abc">beacon</a></p>
    """
    parsed = _parse(html)
    urls = [link.url for link in parsed.links]
    assert urls == ["https://www.bloomberg.com/news/articles/2026-04-30/fed"]


def test_bloomberg_direct_url_strips_utm_and_cmpid() -> None:
    href = (
        "https://www.bloomberg.com/news/articles/2026-04-30/fed"
        "?cmpid=BBD0430_morningamer&utm_campaign=morningamer&utm_medium=email&keep=yes"
    )
    html = f'<p><a href="{href}">Fed cuts</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == "https://www.bloomberg.com/news/articles/2026-04-30/fed?keep=yes"


def test_information_am_briefing_strips_utm() -> None:
    href = (
        "https://www.theinformation.com/briefings/anthropic-funding"
        "?utm_campaign=cio&utm_medium=email&utm_source=cio"
    )
    html = f'<p><a href="{href}">Anthropic funding</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == "https://www.theinformation.com/briefings/anthropic-funding"


def test_information_opaque_wrapper_falls_through_unchanged() -> None:
    # urlNNNN.theinformation.com/ls/click?upn=u001.<payload> is server-side
    # opaque; kept as-is.
    wrapped = (
        "https://url3396.theinformation.com/ls/click"
        "?upn=u001.71kYkaWDpGOJSzbGrs4y1TNF0-2FB-2Bh5pDUdkL0JSEoBlvYCYiS"
    )
    html = f'<p><a href="{wrapped}">opaque link</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == wrapped


def test_unknown_wrapper_falls_through_unchanged() -> None:
    href = "https://some-unknown-tracker.example/click?id=42"
    html = f'<p><a href="{href}">link</a></p>'
    parsed = _parse(html)
    assert parsed.links[0].url == href


def test_paragraph_breaks_preserved_between_blocks() -> None:
    html = "<p>First paragraph.</p><p>Second paragraph.</p>"
    parsed = _parse(html)
    assert "First paragraph." in parsed.plain_text
    assert "Second paragraph." in parsed.plain_text
    # Should be on separate lines, not smushed together.
    first_line, _, rest = parsed.plain_text.partition("\n")
    assert "Second paragraph." not in first_line
    assert "Second paragraph." in rest


def test_empty_anchor_is_dropped_without_breaking_ids() -> None:
    html = """
    <p><a href="https://a.example">good</a>
    <a href=""></a>
    <a href="https://b.example">also good</a></p>
    """
    parsed = _parse(html)
    assert [link.id for link in parsed.links] == [1, 2]


def test_script_and_style_blocks_are_stripped() -> None:
    html = """
    <style>.a{color:red}</style>
    <script>alert('x')</script>
    <p>Real text.</p>
    """
    parsed = _parse(html)
    assert "color:red" not in parsed.plain_text
    assert "alert" not in parsed.plain_text
    assert "Real text." in parsed.plain_text


_MARKER_RE = re.compile(r"\[(\d+)\]")


@pytest.mark.skipif(
    not SAMPLE_DIR.is_dir(),
    reason=f"{SAMPLE_DIR} not populated; skip real-fixture tests",
)
def test_real_fixtures_per_sender() -> None:
    """Parse every fixture and assert the parser's invariants hold per sender.

    Layout is <sample_dir>/<sender_slug>/<message_id>.{html,json}. The
    parent directory name is the sender slug because the sidecar JSON's
    sender field is the full From header, not a slug.
    """
    html_files = sorted(SAMPLE_DIR.rglob("*.html"))
    if not html_files:
        pytest.skip(f"{SAMPLE_DIR} exists but has no *.html fixtures yet")

    sender_seen: dict[str, int] = {}
    for html_path in html_files:
        meta_path = html_path.with_suffix(".json")
        assert meta_path.exists(), f"missing metadata sidecar for {html_path.name}"
        meta = json.loads(meta_path.read_text())
        sender_slug = html_path.parent.name

        parsed = parse_email_html(
            html_path.read_text(),
            message_id=meta["message_id"],
            sender=sender_slug,
            subject=meta["subject"],
            received_at=parsedate_to_datetime(meta["received_at_utc_iso"]),
        )

        assert parsed.plain_text.strip(), f"empty body for {html_path.name}"
        assert parsed.links, f"no links extracted from {html_path.name}"

        link_ids = {link.id for link in parsed.links}
        markers = {int(m) for m in _MARKER_RE.findall(parsed.plain_text)}
        missing = markers - link_ids
        assert not missing, f"{html_path.name}: markers {sorted(missing)} have no matching Link"

        sender_seen[parsed.sender] = sender_seen.get(parsed.sender, 0) + 1

    assert sender_seen, "no senders represented in fixtures"
