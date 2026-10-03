from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class OpenRouterCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(min_length=1, max_length=4096)


class ProviderCredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    configured: bool = True
    created_at: datetime
    updated_at: datetime
