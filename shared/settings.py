"""Process-wide settings loaded from environment variables.

get_settings() caches the parsed Settings instance. Tests override by
setting env vars before the first call or by clearing the cache.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str
    anthropic_api_key: str
    # Anthropic Admin API key, distinct from anthropic_api_key. Only the
    # daily cost-sync cron (shared.anthropic_admin) needs it; the cron
    # logs a warning and no-ops when missing.
    anthropic_admin_api_key: str | None = None
    notion_token: str
    gmail_client_id: str
    gmail_client_secret: str
    gmail_refresh_token: str

    # ---- Workspace-specific identifiers ---------------------------------
    # Not secrets, but personal to a deployment: account addresses, cloud
    # resource names, live endpoint URLs, Notion IDs. They ship via env
    # (.env.tpl locally, the Modal secret in production) rather than code
    # defaults, so a misconfigured environment fails fast instead of
    # silently using someone else's resources. Empty defaults keep services
    # that don't need a given value bootable.

    # Pub/Sub push pipeline for the Triager.
    gmail_pubsub_topic: str = ""
    # The audience claim Pub/Sub mints into the OIDC token equals the
    # configured push endpoint URL. The JWT verifier in
    # services/triager/api.py compares the incoming aud claim to this
    # value, so an empty setting rejects every push (fail closed).
    gmail_pubsub_audience: str = ""
    gmail_pubsub_push_sa: str = ""

    # Mailbox to watch. Other accounts forward to this one, so the
    # production Triager only needs a single watch.
    gmail_watch_email: str = ""

    # Observability config. All optional; agents short-circuit cleanly
    # when keys are unset (local dev runs without external observability).
    # LANGSMITH_TRACING / LANGSMITH_API_KEY / LANGSMITH_PROJECT are also
    # read directly by the langsmith SDK when present in env; we surface
    # them here so callers can log the configured project and tests can
    # mock them.
    langsmith_api_key: str | None = None
    langsmith_tracing: bool = False
    langsmith_project: str = "agents-prod"

    # Shared secret guarding the dashboard-facing API endpoints. When
    # unset (local dev), the bearer dependency allows requests through
    # so curl-driven local debugging stays friction-free; in production
    # it must be set on the Modal Secret + on Vercel so the dashboard's
    # server components can reach the API.
    agents_api_token: str | None = None

    # Base URL for the Job Apps service. The Triager's HTTP dispatch
    # client POSTs to {job_apps_api_url}/internal/dispatch to forward
    # Job-Apps-labeled messages into the LangGraph pipeline running in a
    # separate Modal app.
    job_apps_api_url: str = ""

    # Notion databases for the Job Apps pipeline, plus the News DB
    # (written by news_brief, read by morning_brief).
    notion_news_data_source_id: str = ""
    notion_job_apps_db_id: str = ""
    notion_job_apps_data_source_id: str = ""
    notion_companies_db_id: str = ""
    notion_companies_data_source_id: str = ""

    # Morning Brief workspace config. mb_projects maps a Notion project
    # page id to [display name, background color]; mb_school_project_ids
    # lists project ids whose overdue tasks are stale noise. Both parse
    # from JSON env values.
    mb_tasks_data_source_id: str = ""
    mb_focus_hours_data_source_id: str = ""
    mb_leetcode_data_source_id: str = ""
    mb_briefs_hub_data_source_id: str = ""
    mb_primary_calendar_id: str = ""
    mb_projects: dict[str, list[str]] = {}
    mb_school_project_ids: list[str] = []


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
