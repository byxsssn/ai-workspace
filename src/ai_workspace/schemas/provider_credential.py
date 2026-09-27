from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class OpenRouterCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(max_length=4096)
    base_url: str | None = Field(default=None, max_length=2048)


class ProviderCredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    base_url: str | None
    configured: bool = True
    created_at: datetime
    updated_at: datetime
