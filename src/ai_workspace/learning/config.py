from functools import lru_cache
from typing import Self
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LearningSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_WORKSPACE_LEARNING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    notion_api_token: SecretStr | None = Field(default=None, min_length=1)
    notion_owner_user_id: UUID | None = None
    notion_fastapi_page_id: UUID | None = None
    notion_learning_data_source_id: UUID | None = None

    @field_validator("notion_api_token")
    @classmethod
    def validate_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and any(
            not 33 <= ord(char) <= 126 for char in value.get_secret_value()
        ):
            raise ValueError("Notion token must contain printable ASCII without spaces")
        return value

    @model_validator(mode="after")
    def validate_connection(self) -> Self:
        present = (
            self.notion_api_token is not None,
            self.notion_owner_user_id is not None,
            self.notion_fastapi_page_id is not None,
        )
        if any(present) and not all(present):
            raise ValueError("Notion connection requires token, owner and page ID")
        return self


@lru_cache
def get_learning_settings() -> LearningSettings:
    return LearningSettings()
