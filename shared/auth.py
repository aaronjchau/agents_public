"""Bearer-token auth dependency for the dashboard-facing API endpoints.

AGENTS_API_TOKEN is the shared secret between the Vercel dashboard and
the Modal services; apply per route via Depends(verify_bearer). Public
endpoints and the Pub/Sub-authenticated webhook deliberately skip it.

Design notes: docs/design.md.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, status

from shared.settings import get_settings


def verify_bearer(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency that enforces Authorization: Bearer <token>.

    No-op when the token setting is unset locally (dev convenience).
    Fails closed with a 500 when it is unset in a remote Modal container:
    a missing prod secret is a misconfiguration, not an invitation to
    serve open traffic. A configured token rejects missing or
    non-matching headers with 401.

    The comparison uses secrets.compare_digest so the match path leaks no
    timing information, and both operands are encoded to bytes first:
    compare_digest raises TypeError on a str holding non-ASCII, and the
    presented token is attacker-controlled (Starlette latin-1-decodes raw
    header bytes), so a byte 0x80-0xFF would otherwise turn a 401 into an
    unauthenticated 500.
    """
    expected = get_settings().agents_api_token
    if expected is None:
        # MODAL_IS_REMOTE is set only inside remote containers; MODAL_ENVIRONMENT
        # is not a reliable prod signal (it's set in every remote container).
        if os.environ.get("MODAL_IS_REMOTE") == "1":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="server auth misconfigured: bearer secret unset",
            )
        return

    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    presented = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
