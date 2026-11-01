"""学习地图的只读记录结构，与固定 FastAPI 学习上下文分开。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from ai_workspace.learning.models import LearningModel

type LearningRecordSource = Literal["demo", "notion"]


class LearningRecordSummary(LearningModel):
    id: UUID
    topic_id: Literal["fastapi"] | None = None
    topic_name: str
    category: str | None = None
    priority: str | None = None
    source: LearningRecordSource
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
    warnings: list[str] = Field(default_factory=list)


class LearningRecordDetail(LearningRecordSummary):
    notes_markdown: str
    notes_complete: bool


class LearningRecordList(LearningModel):
    source: LearningRecordSource
    items: list[LearningRecordSummary]
    fetched_at: datetime
    has_more: bool
    warnings: list[str] = Field(default_factory=list)
