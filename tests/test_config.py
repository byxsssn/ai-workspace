from pathlib import Path

from pytest import MonkeyPatch

from ai_workspace.core.config import Settings, get_settings

CONFIG_ENV_VARS = (
    "AI_WORKSPACE_APP_NAME",
    "AI_WORKSPACE_APP_VERSION",
    "AI_WORKSPACE_ENVIRONMENT",
    "AI_WORKSPACE_DEBUG",
    "AI_WORKSPACE_DATABASE_URL",
)


def test_default_settings(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    settings = Settings()

    assert settings.app_name == "AI Workspace"
    assert settings.app_version == "0.1.0"
    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.database_url == (
        "postgresql+asyncpg://ai_workspace:ai_workspace@localhost:5432/ai_workspace"
    )


def test_dotenv_settings_and_environment_overrides(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "AI_WORKSPACE_APP_NAME=Dotenv Workspace\n"
        "AI_WORKSPACE_APP_VERSION=0.2.0\n"
        "AI_WORKSPACE_ENVIRONMENT=testing\n"
        "AI_WORKSPACE_DEBUG=true\n",
        encoding="utf-8",
    )

    dotenv_settings = Settings()

    assert dotenv_settings.app_name == "Dotenv Workspace"
    assert dotenv_settings.app_version == "0.2.0"
    assert dotenv_settings.environment == "testing"
    assert dotenv_settings.debug is True

    monkeypatch.setenv("AI_WORKSPACE_APP_NAME", "Environment Workspace")
    monkeypatch.setenv("AI_WORKSPACE_DEBUG", "false")
    database_url = "postgresql+asyncpg://test:test@localhost:5433/test_workspace"
    monkeypatch.setenv("AI_WORKSPACE_DATABASE_URL", database_url)

    overridden_settings = Settings()

    assert overridden_settings.app_name == "Environment Workspace"
    assert overridden_settings.app_version == "0.2.0"
    assert overridden_settings.environment == "testing"
    assert overridden_settings.debug is False
    assert overridden_settings.database_url == database_url


def test_get_settings_is_cached(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    try:
        settings = get_settings()
        monkeypatch.setenv("AI_WORKSPACE_APP_NAME", "Changed Workspace")

        assert get_settings() is settings
        assert get_settings().app_name == "AI Workspace"
    finally:
        get_settings.cache_clear()
