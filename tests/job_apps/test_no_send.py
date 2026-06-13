"""CI-enforced hard rule: users.messages.send must never appear.

The pipeline never auto-sends email; it only creates Gmail drafts that
are reviewed and sent manually. The check is structural (AST), not
textual: grepping for "send" false-positives on sockets and HTTP
clients, so the scanner walks attribute chains and anchors on the
messages segment. users.drafts.send (sending an existing draft) and
unrelated .send methods are deliberately not flagged, and only
production code under services/job_apps/ is scanned. If this test ever
flags, never silence it; fix the call site to use drafts.create.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Production source under guard. Tests are deliberately not scanned;
# mocks may legitimately reference messages.send call counts to assert
# they are zero.
_JOB_APPS_DIR = Path(__file__).resolve().parents[2] / "services" / "job_apps"


def _attribute_chain(node: ast.AST) -> list[str]:
    """Walk an attribute/call chain bottom-up and return the attribute names.

    For api.users().messages().send(...) this returns ['users',
    'messages', 'send'], innermost first. Non-attribute receivers yield
    an empty list.
    """
    chain: list[str] = []
    current: ast.AST | None = node
    while isinstance(current, ast.Attribute):
        chain.append(current.attr)
        # users() parses as a Call wrapping the Attribute named users;
        # unwrap the Call to keep walking.
        current = current.value.func if isinstance(current.value, ast.Call) else current.value
    return list(reversed(chain))


def _scan_source(source: str) -> list[tuple[int, str]]:
    """Return (lineno, snippet) pairs where a messages.send chain appears.

    Catches the chained Gmail shape regardless of receiver alias.
    Misses, by design: users.drafts().send(...) (sends an existing
    draft, not new mail) and unrelated .send methods without messages
    in the receiver chain.
    """
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr != "send":
            continue
        chain = _attribute_chain(node)
        # send must come immediately after messages in the chain so
        # drafts().send() does not trip the guard.
        if "messages" in chain and "send" in chain:
            try:
                msg_idx = chain.index("messages")
                send_idx = chain.index("send")
            except ValueError:  # pragma: no cover - checked above
                continue
            if send_idx == msg_idx + 1:
                hits.append((node.lineno, ".".join(chain)))
    return hits


def _scan_file_for_messages_send(path: Path) -> list[tuple[int, str]]:
    return _scan_source(path.read_text(encoding="utf-8"))


def test_no_messages_send_in_job_apps_source() -> None:
    """No file under services/job_apps/ may contain a messages.send call chain.

    If this fails, never silence it; the pipeline ships drafts only.
    Fix the offending call site to use users.drafts.create.
    """
    offenders: list[str] = []
    for py_file in _JOB_APPS_DIR.rglob("*.py"):
        for lineno, chain in _scan_file_for_messages_send(py_file):
            offenders.append(f"{py_file}:{lineno} → {chain}")

    if offenders:
        pytest.fail(
            "Forbidden `messages.send` call(s) found under services/job_apps/. "
            "The Job Apps pipeline must NEVER auto-send email — drafts only. "
            "Use `users.drafts.create` instead.\n  " + "\n  ".join(offenders)
        )


def test_scan_helper_detects_canonical_messages_send_pattern() -> None:
    """The AST scanner catches the canonical bad pattern.

    Without this self-test, a scanner regression would silently disable
    the safety net.
    """
    bad_source = (
        "def send_email(service):\n"
        "    return service.users().messages().send(userId='me', body={}).execute()\n"
    )
    hits = _scan_source(bad_source)

    assert len(hits) == 1, (
        f"Scanner failed to detect canonical `messages.send` pattern; got hits: {hits}"
    )


def test_scan_helper_does_not_flag_drafts_send() -> None:
    """drafts.send is not flagged; it sends an existing draft, not new mail.

    No current code path calls drafts.send; if a future feature exposes
    one, add a separate explicit guard for it.
    """
    drafts_send_source = (
        "def send_existing_draft(service, draft_id):\n"
        "    return service.users().drafts().send(userId='me', body={'id': draft_id}).execute()\n"
    )
    hits = _scan_source(drafts_send_source)

    assert hits == [], f"Scanner false-positive on drafts.send: {hits}"


def test_scan_helper_does_not_flag_unrelated_send_methods() -> None:
    """Unrelated .send methods (sockets, HTTP clients) are not flagged."""
    unrelated_source = (
        "def doit(client, sock):\n"
        "    sock.send(b'hello')\n"
        "    client.send_request('GET', '/')\n"
        "    return client.connection.send(b'data')\n"
    )
    hits = _scan_source(unrelated_source)

    assert hits == [], f"Scanner false-positive on unrelated .send methods: {hits}"
