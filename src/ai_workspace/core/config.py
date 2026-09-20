from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_WORKSPACE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Workspace"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    jwt_secret_key: SecretStr = Field(min_length=32)
    jwt_access_token_expire_minutes: int = Field(default=30, gt=0)
    database_url: str = (
        "postgresql+asyncpg://ai_workspace:ai_workspace@localhost:5432/ai_workspace"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
