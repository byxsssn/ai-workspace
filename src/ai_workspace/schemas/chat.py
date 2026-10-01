from pydantic import BaseModel, ConfigDict, Field

from ai_workspace.providers import TokenUsage


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ChatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    content: str
    model: str
    usage: TokenUsage | None = None
