from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type ReasoningEffort = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh", "max"
]


class ProviderId(StrEnum):
    OPENROUTER = "openrouter"


class ModelMessage(BaseModel):
    """独立于数据库和 HTTP 接口结构的文本消息。"""

    model_config = ConfigDict(strict=True)

    role: Literal["system", "user", "assistant"]
    content: str


class TokenUsage(BaseModel):
    """供应商返回的 Token 计数；缺失时保持未知，不补零。"""

    model_config = ConfigDict(strict=True)

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ModelResponse(BaseModel):
    """一次生成得到的有效文本结果，独立于底层传输协议。"""

    model_config = ConfigDict(strict=True)

    content: str = Field(min_length=1)
    model: str = Field(min_length=1)
    usage: TokenUsage | None = None
