from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.db.session import get_db_session
from ai_workspace.learning.config import LearningSettings, get_learning_settings
from ai_workspace.learning.http import create_source_client
from ai_workspace.learning.service import LearningService
from ai_workspace.models import User


async def get_learning_user_id(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UUID:
    """本组接口只读：先结束鉴权查询事务，再等待外部网络。"""
    user_id = user.id
    await session.rollback()
    return user_id


async def get_learning_service(
    settings: Annotated[LearningSettings, Depends(get_learning_settings)],
) -> AsyncIterator[LearningService]:
    async with create_source_client() as client:
        yield LearningService(settings, client)
