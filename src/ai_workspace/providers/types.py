from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """A text message independent of persistence and HTTP API schemas."""

    model_config = ConfigDict(strict=True)

    role: Literal["system", "user", "assistant"]
    content: str


class TokenUsage(BaseModel):
    """Reported token counts; missing counts remain unknown, not zero."""

    model_config = ConfigDict(strict=True)

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ChatCompletionResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    content: str
    model: str = Field(min_length=1)
    usage: TokenUsage | None = None
