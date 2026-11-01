import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest
from pydantic import ValidationError

from ai_workspace.learning.config import LearningSettings
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.service import LearningService


def test_connection_requires_owner_and_page_and_hides_token():
    token = "secret-test-notion-token"
    with pytest.raises(ValidationError) as exc_info:
        LearningSettings(_env_file=None, notion_api_token=token)
    assert token not in str(exc_info.value)
    settings = LearningSettings(
        _env_file=None,
        notion_api_token=token,
        notion_owner_user_id=uuid4(),
        notion_fastapi_page_id=uuid4(),
    )
    assert token not in repr(settings)


@pytest.mark.parametrize("token", ["", "with space", "with\nnewline", "中文"])
def test_invalid_connection_token_is_rejected(token):
    with pytest.raises(ValidationError):
        LearningSettings(
            _env_file=None,
            notion_api_token=token,
            notion_owner_user_id=uuid4(),
            notion_fastapi_page_id=uuid4(),
        )


def test_demo_is_explicit_and_never_calls_notion():
    async def run():
        async with httpx2.AsyncClient() as client:
            service = LearningService(LearningSettings(_env_file=None), client)
            service.notion.read = AsyncMock()
            result = await service.read_state(user_id=uuid4(), source="demo")
            assert result.source == "demo"
            assert result.source_url is None
            assert result.evidence_url is None
            assert result.source_updated_at is None
            assert "demo_data_not_personal_progress" in result.warnings
            service.notion.read.assert_not_called()

    asyncio.run(run())


def test_notion_without_configuration_does_not_fall_back_to_demo():
    async def run():
        async with httpx2.AsyncClient() as client:
            service = LearningService(LearningSettings(_env_file=None), client)
            with pytest.raises(LearningSourceError) as exc_info:
                await service.read_state(user_id=uuid4(), source="notion")
            assert exc_info.value.code == "notion_not_configured"

    asyncio.run(run())


def test_only_configured_owner_can_use_connection():
    async def run():
        owner = uuid4()
        page_id = uuid4()
        settings = LearningSettings(
            _env_file=None,
            notion_api_token="secret-test-notion-token",
            notion_owner_user_id=owner,
            notion_fastapi_page_id=page_id,
        )
        async with httpx2.AsyncClient() as client:
            service = LearningService(settings, client)
            service.notion.read = AsyncMock()
            with pytest.raises(LearningSourceError) as exc_info:
                await service.read_state(user_id=uuid4(), source="notion")
            assert exc_info.value.code == "notion_not_available"
            service.notion.read.assert_not_called()
            await service.read_state(user_id=owner, source="notion")
            service.notion.read.assert_awaited_once_with(
                token="secret-test-notion-token", page_id=page_id
            )

    asyncio.run(run())
