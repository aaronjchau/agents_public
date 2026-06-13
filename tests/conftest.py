"""Test fixtures and environment defaults.

Sets dummy values for the env vars Settings requires, skipping the dummy
when AGENTS_LIVE_LLM=1 since live tests need the real ANTHROPIC_API_KEY
from .env (pydantic-settings reads os.environ before .env, so a dummy
here would shadow it). Also hosts the FakeProc and capturing_run doubles
for the shared ntn subprocess wrapper.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

if os.environ.get("AGENTS_LIVE_LLM") != "1":
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-api-key")
    os.environ.setdefault("NOTION_TOKEN", "test-notion-token")

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("GMAIL_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "test-gmail-client-secret")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "test-gmail-refresh-token")

# Workspace identifiers have empty defaults in Settings; tests get stable
# synthetic values so modules that read them at import time resolve.
os.environ.setdefault("GMAIL_PUBSUB_TOPIC", "projects/test-project/topics/test-topic")
os.environ.setdefault("GMAIL_PUBSUB_AUDIENCE", "https://example.com/gmail-webhook")
os.environ.setdefault("GMAIL_PUBSUB_PUSH_SA", "test-push@test-project.iam.gserviceaccount.com")
os.environ.setdefault("GMAIL_WATCH_EMAIL", "watch@example.com")
os.environ.setdefault("JOB_APPS_API_URL", "https://job-apps-api.example.com")
os.environ.setdefault("NOTION_JOB_APPS_DB_ID", "00000000-0000-4000-8000-0000000000a1")
os.environ.setdefault("NOTION_JOB_APPS_DATA_SOURCE_ID", "00000000-0000-4000-8000-0000000000a2")
os.environ.setdefault("NOTION_COMPANIES_DB_ID", "00000000-0000-4000-8000-0000000000b1")
os.environ.setdefault("NOTION_COMPANIES_DATA_SOURCE_ID", "00000000-0000-4000-8000-0000000000b2")
os.environ.setdefault("MB_TASKS_DATA_SOURCE_ID", "00000000-0000-4000-8000-0000000000c1")
os.environ.setdefault("MB_FOCUS_HOURS_DATA_SOURCE_ID", "00000000-0000-4000-8000-0000000000c2")
os.environ.setdefault("MB_LEETCODE_DATA_SOURCE_ID", "00000000-0000-4000-8000-0000000000c3")
os.environ.setdefault("MB_BRIEFS_HUB_DATA_SOURCE_ID", "00000000-0000-4000-8000-0000000000c4")
os.environ.setdefault("NOTION_NEWS_DATA_SOURCE_ID", "00000000-0000-4000-8000-0000000000c5")
os.environ.setdefault("MB_PRIMARY_CALENDAR_ID", "calendar@example.com")
os.environ.setdefault(
    "MB_PROJECTS",
    '{"proj-dsa": ["DSA", "blue_bg"], "proj-swe": ["SWE", "orange_bg"],'
    ' "proj-portfolio": ["Portfolio", "purple_bg"],'
    ' "proj-networking": ["Networking", "gray_bg"], "proj-inbox": ["Inbox", ""]}',
)
os.environ.setdefault("MB_SCHOOL_PROJECT_IDS", '["proj-cs106b", "proj-cs103"]')


@dataclass
class FakeProc:
    """Stand-in for the CompletedProcess returned by subprocess.run."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    args: Any = None


def capturing_run(
    *procs: FakeProc,
) -> tuple[Callable[..., FakeProc], list[dict[str, Any]]]:
    """Return a subprocess.run stand-in that yields procs in order.

    Also returns a list capturing each call's cmd and env.
    """
    captured: list[dict[str, Any]] = []
    proc_iter = iter(procs)

    def _run(cmd: list[str], **kwargs: Any) -> FakeProc:
        captured.append({"cmd": cmd, "env": kwargs.get("env")})
        return next(proc_iter)

    return _run, captured
