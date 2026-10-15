from pydantic import BaseModel, ConfigDict, Field

from ai_workspace.providers import ReasoningEffort, TokenUsage


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    content: str = Field(min_length=1)
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description=(
            "本轮推理强度。省略或传入 null 时使用模型默认值；"
            "'none' 表示请求关闭推理。支持的档位取决于具体模型。"
        ),
    )


class ChatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    content: str
    model: str
    usage: TokenUsage | None = None
