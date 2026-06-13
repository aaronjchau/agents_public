"""Factory for an Anthropic client with optional LangSmith instrumentation.

Every production flow builds its client here so the tracing wiring lives
in one place. Callers still capture response.usage themselves: tracing is
observability, the audit row is accounting.
"""

from __future__ import annotations

from anthropic import Anthropic
from langsmith.wrappers import wrap_anthropic

from shared.settings import get_settings


def get_anthropic_client(api_key: str | None = None) -> Anthropic:
    """Return an Anthropic client, LangSmith-wrapped when tracing is enabled.

    api_key defaults to settings.anthropic_api_key. The LangSmith wrapper
    is a drop-in for the plain client.
    """
    settings = get_settings()
    client = Anthropic(api_key=api_key or settings.anthropic_api_key)

    if settings.langsmith_tracing and settings.langsmith_api_key:
        client = wrap_anthropic(client)

    return client
