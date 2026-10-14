from functools import lru_cache
from pathlib import Path
from typing import Self

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_WORKSPACE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "AI Workspace"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    jwt_secret_key: SecretStr = Field(min_length=32)
    jwt_access_token_expire_minutes: int = Field(default=30, gt=0)
    credential_encryption_key: SecretStr
    registration_invite_code: SecretStr | None = Field(default=None, min_length=32)
    frontend_dist: Path = Path("frontend/dist")
    database_url: str = Field(
        default="postgresql+asyncpg://ai_workspace:ai_workspace@localhost:5432/ai_workspace",
        repr=False,
    )

    @field_validator("environment")
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        try:
            url = make_url(value)
            if url.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
                raise ValueError
            return url.set(drivername="postgresql+asyncpg").render_as_string(
                hide_password=False
            )
        except ValueError, ArgumentError:
            # SQLAlchemy's parsing errors can contain the original connection URL.
            raise ValueError("A valid PostgreSQL connection URL is required") from None

    @field_validator("credential_encryption_key")
    @classmethod
    def validate_credential_encryption_key(cls, value: SecretStr) -> SecretStr:
        try:
            Fernet(value.get_secret_value())
        except ValueError:
            raise ValueError(
                "Credential encryption key must be a valid Fernet key"
            ) from None
        return value

    @model_validator(mode="after")
    def validate_production(self) -> Self:
        if self.environment == "production":
            if self.debug:
                raise ValueError("Debug must be disabled in production")
            if "database_url" not in self.model_fields_set:
                raise ValueError("A production database URL is required")
            if not self.registration_invite_code:
                raise ValueError("A production registration invite code is required")
            if self.jwt_secret_key.get_secret_value().startswith("replace-with-"):
                raise ValueError("A generated production JWT secret is required")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
