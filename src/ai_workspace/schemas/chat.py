from pydantic import BaseModel, ConfigDict, Field

from ai_workspace.providers import ReasoningEffort, TokenUsage


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    content: str = Field(min_length=1)
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description=(
            "Reasoning effort for this turn. Omit or use null for the model default; "
            "'none' requests no reasoning. Supported levels depend on the model."
        ),
    )


class ChatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    content: str
    model: str
    usage: TokenUsage | None = None
