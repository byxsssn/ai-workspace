from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from pytest import MonkeyPatch

from ai_workspace.core.config import Settings, get_settings

CONFIG_ENV_VARS = (
    "AI_WORKSPACE_APP_NAME",
    "AI_WORKSPACE_APP_VERSION",
    "AI_WORKSPACE_ENVIRONMENT",
    "AI_WORKSPACE_DEBUG",
    "AI_WORKSPACE_DATABASE_URL",
    "AI_WORKSPACE_JWT_SECRET_KEY",
    "AI_WORKSPACE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
)
TEST_JWT_SECRET_KEY = "non-sensitive-test-jwt-secret-key-123456"


def test_default_settings(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_WORKSPACE_JWT_SECRET_KEY", TEST_JWT_SECRET_KEY)
    settings = Settings()

    assert settings.app_name == "AI Workspace"
    assert settings.app_version == "0.1.0"
    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.database_url == (
        "postgresql+asyncpg://ai_workspace:ai_workspace@localhost:5432/ai_workspace"
    )
    assert isinstance(settings.jwt_secret_key, SecretStr)
    assert settings.jwt_secret_key.get_secret_value() == TEST_JWT_SECRET_KEY
    assert TEST_JWT_SECRET_KEY not in repr(settings)
    assert settings.jwt_access_token_expire_minutes == 30


def test_dotenv_settings_and_environment_overrides(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    dotenv_secret = "dotenv-non-sensitive-jwt-secret-key-123456"
    (tmp_path / ".env").write_text(
        "AI_WORKSPACE_APP_NAME=Dotenv Workspace\n"
        "AI_WORKSPACE_APP_VERSION=0.2.0\n"
        "AI_WORKSPACE_ENVIRONMENT=testing\n"
        "AI_WORKSPACE_DEBUG=true\n"
        f"AI_WORKSPACE_JWT_SECRET_KEY={dotenv_secret}\n"
        "AI_WORKSPACE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES=45\n",
        encoding="utf-8",
    )

    dotenv_settings = Settings()

    assert dotenv_settings.app_name == "Dotenv Workspace"
    assert dotenv_settings.app_version == "0.2.0"
    assert dotenv_settings.environment == "testing"
    assert dotenv_settings.debug is True
    assert dotenv_settings.jwt_secret_key.get_secret_value() == dotenv_secret
    assert dotenv_secret not in repr(dotenv_settings)
    assert dotenv_settings.jwt_access_token_expire_minutes == 45

    monkeypatch.setenv("AI_WORKSPACE_APP_NAME", "Environment Workspace")
    monkeypatch.setenv("AI_WORKSPACE_DEBUG", "false")
    database_url = "postgresql+asyncpg://test:test@localhost:5433/test_workspace"
    monkeypatch.setenv("AI_WORKSPACE_DATABASE_URL", database_url)
    monkeypatch.setenv("AI_WORKSPACE_JWT_SECRET_KEY", TEST_JWT_SECRET_KEY)
    monkeypatch.setenv("AI_WORKSPACE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60")

    overridden_settings = Settings()

    assert overridden_settings.app_name == "Environment Workspace"
    assert overridden_settings.app_version == "0.2.0"
    assert overridden_settings.environment == "testing"
    assert overridden_settings.debug is False
    assert overridden_settings.database_url == database_url
    assert overridden_settings.jwt_secret_key.get_secret_value() == TEST_JWT_SECRET_KEY
    assert TEST_JWT_SECRET_KEY not in repr(overridden_settings)
    assert overridden_settings.jwt_access_token_expire_minutes == 60


def test_get_settings_is_cached(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_WORKSPACE_JWT_SECRET_KEY", TEST_JWT_SECRET_KEY)
    get_settings.cache_clear()

    try:
        settings = get_settings()
        monkeypatch.setenv("AI_WORKSPACE_APP_NAME", "Changed Workspace")

        assert get_settings() is settings
        assert get_settings().app_name == "AI Workspace"
        assert settings.jwt_secret_key.get_secret_value() == TEST_JWT_SECRET_KEY
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("secret_key", "expire_minutes", "error_field"),
    [
        pytest.param(None, 30, "jwt_secret_key", id="missing-secret"),
        pytest.param("", 30, "jwt_secret_key", id="empty-secret"),
        pytest.param("x" * 31, 30, "jwt_secret_key", id="short-secret"),
        pytest.param(
            TEST_JWT_SECRET_KEY,
            0,
            "jwt_access_token_expire_minutes",
            id="zero-expiry",
        ),
        pytest.param(
            TEST_JWT_SECRET_KEY,
            -1,
            "jwt_access_token_expire_minutes",
            id="negative-expiry",
        ),
    ],
)
def test_invalid_jwt_settings_are_rejected(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    secret_key: str | None,
    expire_minutes: int,
    error_field: str,
) -> None:
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    if secret_key is not None:
        monkeypatch.setenv("AI_WORKSPACE_JWT_SECRET_KEY", secret_key)
    monkeypatch.setenv(
        "AI_WORKSPACE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES", str(expire_minutes)
    )

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert {error["loc"] for error in exc_info.value.errors()} == {(error_field,)}
