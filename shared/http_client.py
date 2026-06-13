"""HTTP client bridging the Triager and Job Apps Modal apps.

Hard contract: never raises. The Triager webhook must keep returning 200
to Pub/Sub; if this client raised, Pub/Sub would retry and re-dispatch
the same message indefinitely.
"""

from __future__ import annotations

import logging

import httpx

from shared.settings import get_settings

logger = logging.getLogger(__name__)

DISPATCH_TIMEOUT_S = 30.0  # Pub/Sub ACK budget is about 60s; the graph takes 8-15s.


async def dispatch_job_apps(message_id: str) -> None:
    """POST message_id to job-apps-api's /internal/dispatch.

    Bearer auth via AGENTS_API_TOKEN. Blocks until the graph completes or
    the timeout expires. All errors are swallowed with a structured log;
    the Triager webhook path MUST NOT see exceptions from this function.
    """
    settings = get_settings()
    url = f"{settings.job_apps_api_url}/internal/dispatch"
    # Only attach the header when the token is set; otherwise a literal
    # "Bearer None" would be sent.
    headers = (
        {"Authorization": f"Bearer {settings.agents_api_token}"}
        if settings.agents_api_token
        else {}
    )
    try:
        async with httpx.AsyncClient(timeout=DISPATCH_TIMEOUT_S) as client:
            resp = await client.post(
                url,
                json={"message_id": message_id},
                headers=headers,
            )
            if resp.status_code >= 400:
                # Don't log the response body; upstream error details can
                # echo email PII. Status and message_id are enough to triage.
                logger.warning(
                    "dispatch_job_apps.non_2xx",
                    extra={
                        "message_id": message_id,
                        "status": resp.status_code,
                    },
                )
    except Exception:
        logger.exception(
            "dispatch_job_apps.failed",
            extra={"message_id": message_id},
        )
