from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    database_url: str = (
        "postgresql+asyncpg://ai_workspace:ai_workspace@localhost:5432/ai_workspace"
    )

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
