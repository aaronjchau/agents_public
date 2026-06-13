"""Mint a Google OAuth refresh token via the installed-app flow (stdlib only).

Run on a machine with a browser: make run-script f=scripts/mint_google_token.py.
Reads GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET from the environment and prints the
new refresh token (scopes: gmail.modify + calendar.readonly). Afterwards, paste
the token into 1Password (op://<vault>/<item>/GMAIL_REFRESH_TOKEN), run
scripts/sync-secrets.sh, and send any email to the watched inbox so the next
Pub/Sub push backfills from the stored history cursor.
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import ClassVar

SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
)
PORT = 8765
REDIRECT_URI = f"http://localhost:{PORT}/"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

_RESPONSE_PAGE = (
    b"<html><body><h2>Token minted &mdash; you can close this tab</h2>"
    b"<p>Return to the terminal for the refresh token.</p></body></html>"
)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures the one OAuth redirect and stores its query params."""

    result: ClassVar[dict[str, str]] = {}
    done: ClassVar[threading.Event] = threading.Event()

    def do_GET(self) -> None:
        query = urllib.parse.urlparse(self.path).query
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        type(self).result = params
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_RESPONSE_PAGE)
        type(self).done.set()

    def log_message(self, *args: object) -> None:
        pass


def main() -> int:
    # strip() guards against trailing whitespace/newlines in the stored
    # 1Password fields; Google 401s (invalid_client) on a padded secret.
    client_id = (os.environ.get("GMAIL_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GMAIL_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        print(
            "GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET not set. Run via:\n"
            "    make run-script f=scripts/mint_google_token.py",
            file=sys.stderr,
        )
        return 1
    # Client ids are public (they appear in the auth URL); print for
    # cross-checking against the GCP console when the exchange fails.
    print(f"Using OAuth client: {client_id}")

    state = secrets.token_urlsafe(16)
    auth_url = (
        AUTH_ENDPOINT
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                # offline plus forced consent makes Google issue a new refresh token.
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )
    )

    server = http.server.HTTPServer(("localhost", PORT), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print("Opening Google consent page (authorize as the watched account)...")
    print(f"If no browser opens, visit:\n\n{auth_url}\n")
    webbrowser.open(auth_url)

    if not _CallbackHandler.done.wait(timeout=300):
        print("Timed out waiting for the OAuth redirect (5 min).", file=sys.stderr)
        return 1
    server.shutdown()

    params = _CallbackHandler.result
    if params.get("state") != state:
        print("State mismatch on the OAuth redirect — aborting.", file=sys.stderr)
        return 1
    code = params.get("code")
    if not code:
        print(f"No auth code in redirect: {params.get('error', params)!r}", file=sys.stderr)
        return 1

    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode("ascii")
    request = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"\nToken exchange failed: HTTP {exc.code}\n{detail}", file=sys.stderr)
        if exc.code == 401:
            print(
                "\n401 = invalid_client: the client secret does not match this "
                "client id.\nFix: GCP console -> Credentials -> this OAuth client "
                "-> Client secrets -> Add/Reset secret,\nupdate "
                "op://<vault>/<item>/GMAIL_CLIENT_SECRET, and rerun.",
                file=sys.stderr,
            )
        return 1

    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        print(
            "Token exchange succeeded but returned NO refresh_token.\n"
            "Revoke the app's existing access at "
            "https://myaccount.google.com/permissions and rerun.",
            file=sys.stderr,
        )
        return 1

    print("\nGranted scopes:", payload.get("scope", "(not reported)"))
    print("\nNEW REFRESH TOKEN (paste into op://<vault>/<item>/GMAIL_REFRESH_TOKEN):\n")
    print(refresh_token)
    print(
        "\nThen: ./scripts/sync-secrets.sh (private repo), and send any email to the watched inbox."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
