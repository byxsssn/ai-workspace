from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="New conversation", max_length=255)
    learning_topic: Literal["fastapi"] | None = None
    learning_source: Literal["demo", "notion"] | None = None

    @model_validator(mode="after")
    def paired_learning_context(self) -> Self:
        if (self.learning_topic is None) != (self.learning_source is None):
            raise ValueError("Learning topic and source must be provided together")
        return self


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    learning_topic: Literal["fastapi"] | None = None
    learning_source: Literal["demo", "notion"] | None = None
    created_at: datetime
    updated_at: datetime


class ConversationUpdateTitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=255)
