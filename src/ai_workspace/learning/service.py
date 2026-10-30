import asyncio
from typing import Literal
from uuid import UUID

import httpx2

from ai_workspace.learning.config import LearningSettings
from ai_workspace.learning.demo import demo_learning_state
from ai_workspace.learning.documents import OfficialDocumentReader
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.models import LearningState, OfficialDocument
from ai_workspace.learning.notion import NotionLearningReader


class LearningService:
    def __init__(self, settings: LearningSettings, client: httpx2.AsyncClient) -> None:
        self.settings = settings
        self.documents = OfficialDocumentReader(client)
        self.notion = NotionLearningReader(client)

    async def read_document(self, section_id: str) -> OfficialDocument:
        return await self.documents.read(section_id)

    async def read_state(
        self, *, user_id: UUID, source: Literal["demo", "notion"]
    ) -> LearningState:
        if source == "demo":
            return demo_learning_state()
        settings = self.settings
        if settings.notion_api_token is None:
            raise LearningSourceError("notion_not_configured", status_code=503)
        if settings.notion_owner_user_id != user_id:
            raise LearningSourceError("notion_not_available", status_code=404)
        # 配置模型保证 token、owner 和 page_id 同时存在。
        assert settings.notion_fastapi_page_id is not None
        try:
            async with asyncio.timeout(30):
                return await self.notion.read(
                    token=settings.notion_api_token.get_secret_value(),
                    page_id=settings.notion_fastapi_page_id,
                )
        except TimeoutError:
            raise LearningSourceError("source_timeout", status_code=504) from None
