from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LearningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class DocumentSection(LearningModel):
    id: str
    title: str
    url: str


class OfficialDocument(LearningModel):
    section_id: str
    title: str
    url: str
    content_markdown: str = Field(min_length=1)
    fetched_at: datetime
    content_sha256: str
    version: str | None = None


class LearningState(LearningModel):
    """应用自己的数据结构，屏蔽 Notion 的属性格式。"""

    topic_id: Literal["fastapi"] = "fastapi"
    topic_name: str
    source: Literal["demo", "notion"]
    source_url: str | None = None
    source_updated_at: datetime | None = None
    fetched_at: datetime
    learning_status: str | None = None
    current_progress: str | None = None
    next_step: str | None = None
    proficiency: str | None = None
    project_practice: bool | None = None
    official_docs_url: str | None = None
    evidence_url: str | None = None
    notes_markdown: str = ""
    notes_complete: bool
    warnings: list[str] = Field(default_factory=list)
