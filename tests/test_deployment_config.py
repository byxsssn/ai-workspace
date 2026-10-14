import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from ai_workspace.core.config import Settings


@pytest.fixture
def production_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    for name in os.environ:
        if name.startswith("AI_WORKSPACE_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    return {
        "environment": "production",
        "database_url": "postgresql://test:test@localhost/test",
        "jwt_secret_key": "non-sensitive-test-jwt-secret-key-123456",
        "credential_encryption_key": Fernet.generate_key().decode(),
        "registration_invite_code": "non-sensitive-test-invite-code-123456",
    }


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+asyncpg"])
def test_normalize_database_url_without_losing_credentials(
    production_values: dict, scheme: str
) -> None:
    production_values["database_url"] = (
        f"{scheme}://app:p%40ss%3A%2F%3F@postgres.railway.internal:5432/db?ssl=require"
    )
    settings = Settings(**production_values)
    url = make_url(settings.database_url)
    assert url.drivername == "postgresql+asyncpg"
    assert url.password == "p@ss:/?"
    assert url.host == "postgres.railway.internal"
    assert url.query == {"ssl": "require"}
    assert "p%40ss" not in repr(settings)


@pytest.mark.parametrize(
    "field",
    [
        "jwt_secret_key",
        "credential_encryption_key",
        "registration_invite_code",
        "database_url",
    ],
)
def test_production_requires_explicit_configuration(
    production_values: dict, field: str
) -> None:
    del production_values[field]
    with pytest.raises(ValidationError):
        Settings(**production_values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("debug", True),
        ("registration_invite_code", ""),
        ("jwt_secret_key", "replace-with-a-random-secret-at-least-32-chars"),
        ("database_url", "sqlite:///should-not-use-this.db"),
        ("database_url", "malformed-connection-sensitive-value"),
    ],
)
def test_unsafe_production_configuration_fails_without_echoing_values(
    production_values: dict, field: str, value: object
) -> None:
    production_values[field] = value
    with pytest.raises(ValidationError) as error:
        Settings(**production_values)
    assert production_values["credential_encryption_key"] not in str(error.value)
    assert "malformed-connection-sensitive-value" not in str(error.value)
