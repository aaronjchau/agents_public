import pytest

from shared.settings import Settings, get_settings


def _set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz")
    monkeypatch.setenv("NOTION_TOKEN", "secret_notion-xyz")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id-xyz")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret-xyz")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh-token-xyz")


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_settings_reads_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    s = Settings()  # type: ignore[call-arg]
    assert s.database_url == "postgresql+asyncpg://u:p@h/db"
    assert s.anthropic_api_key == "sk-ant-xyz"
    assert s.notion_token == "secret_notion-xyz"
    assert s.gmail_client_id == "client-id-xyz"
    assert s.gmail_client_secret == "client-secret-xyz"
    assert s.gmail_refresh_token == "refresh-token-xyz"


def test_settings_ignores_unknown_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("SOMETHING_ELSE", "should-be-ignored")
    s = Settings()  # type: ignore[call-arg]
    assert not hasattr(s, "something_else")


def test_workspace_identifiers_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deployment identifiers default empty so a misconfigured environment
    fails fast instead of silently using another deployment's resources."""
    _set_required(monkeypatch)
    for var in (
        "GMAIL_PUBSUB_TOPIC",
        "GMAIL_PUBSUB_AUDIENCE",
        "GMAIL_PUBSUB_PUSH_SA",
        "GMAIL_WATCH_EMAIL",
        "JOB_APPS_API_URL",
        "NOTION_JOB_APPS_DB_ID",
        "MB_TASKS_DATA_SOURCE_ID",
        "MB_PROJECTS",
    ):
        monkeypatch.delenv(var, raising=False)
    s = Settings()  # type: ignore[call-arg]
    assert s.gmail_pubsub_topic == ""
    assert s.gmail_pubsub_audience == ""
    assert s.gmail_pubsub_push_sa == ""
    assert s.gmail_watch_email == ""
    assert s.job_apps_api_url == ""
    assert s.notion_job_apps_db_id == ""
    assert s.mb_tasks_data_source_id == ""
    assert s.mb_projects == {}


def test_anthropic_admin_api_key_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin key defaults to None so local dev and Modal boot without it."""
    _set_required(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_ADMIN_API_KEY", raising=False)
    s = Settings()  # type: ignore[call-arg]
    assert s.anthropic_admin_api_key is None


def test_anthropic_admin_api_key_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "sk-ant-admin01-xyz")
    s = Settings()  # type: ignore[call-arg]
    assert s.anthropic_admin_api_key == "sk-ant-admin01-xyz"


def test_pubsub_settings_overridable_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", "projects/staging/topics/staging-events")
    monkeypatch.setenv("GMAIL_WATCH_EMAIL", "staging@example.com")
    s = Settings()  # type: ignore[call-arg]
    assert s.gmail_pubsub_topic == "projects/staging/topics/staging-events"
    assert s.gmail_watch_email == "staging@example.com"


def test_mb_collections_parse_from_json_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("MB_PROJECTS", '{"p1": ["Alpha", "blue_bg"], "p2": ["Beta", ""]}')
    monkeypatch.setenv("MB_SCHOOL_PROJECT_IDS", '["p9", "p10"]')
    s = Settings()  # type: ignore[call-arg]
    assert s.mb_projects == {"p1": ["Alpha", "blue_bg"], "p2": ["Beta", ""]}
    assert s.mb_school_project_ids == ["p9", "p10"]
