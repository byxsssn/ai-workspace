from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from ai_workspace.learning.models import LearningModel
from ai_workspace.providers import TokenUsage


class LearningCitation(LearningModel):
    """由实际读取结果生成的来源；模型只选择 source_id，不能创建来源链接。"""

    source_id: str
    kind: Literal["official_document", "learning_record"]
    title: str
    url: str | None = None
    fetched_at: datetime
    content_sha256: str | None = None
    truncated: bool = False


class LearningToolExecution(LearningModel):
    """可展示的调用摘要，不包含密钥、私人正文或模型内部推理。"""

    tool: Literal["get_learning_state", "read_official_doc"]
    section_id: str | None = None
    status: Literal["success", "error"]
    error_code: str | None = None
    cached: bool = False


class LearningAnswer(LearningModel):
    """模型的最终输出；引用还会与本轮实际读取的来源再次核对。"""

    answer: str = Field(min_length=1, max_length=12_000)
    citation_ids: list[str] = Field(default_factory=list, max_length=4)
    status: Literal["answered", "needs_clarification"] = "answered"

    @field_validator("answer")
    @classmethod
    def meaningful_answer(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Answer must not be blank")
        return value


class LearningChatRequest(LearningModel):
    model: str = Field(min_length=1, max_length=200, pattern=r"^\S+$")
    content: str = Field(min_length=1, max_length=8_000)
    source: Literal["demo", "notion"] = "demo"

    @field_validator("content")
    @classmethod
    def meaningful_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Content must not be blank")
        return value


class LearningChatResponse(LearningModel):
    content: str
    model: str
    source: Literal["demo", "notion"]
    status: Literal["answered", "needs_clarification"]
    sources: list[LearningCitation]
    tool_calls: list[LearningToolExecution]
    usage: TokenUsage | None = None
