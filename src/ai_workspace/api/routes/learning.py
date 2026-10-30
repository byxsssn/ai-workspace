from collections.abc import AsyncIterator
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.db.session import get_db_session
from ai_workspace.learning.config import LearningSettings, get_learning_settings
from ai_workspace.learning.documents import SECTIONS
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.http import create_source_client
from ai_workspace.learning.models import (
    DocumentSection,
    LearningState,
    OfficialDocument,
)
from ai_workspace.learning.service import LearningService
from ai_workspace.models import User

router = APIRouter(prefix="/learning/fastapi", tags=["learning"])


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


def source_http_error(error: LearningSourceError) -> HTTPException:
    headers = {"Cache-Control": "no-store"}
    if error.retry_after is not None:
        headers["Retry-After"] = error.retry_after
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": str(error)},
        headers=headers,
    )


@router.get("/sections", response_model=list[DocumentSection])
async def list_sections(
    response: Response,
    user_id: Annotated[UUID, Depends(get_learning_user_id)],
) -> list[DocumentSection]:
    response.headers["Cache-Control"] = "no-store"
    return list(SECTIONS)


@router.get("/sections/{section_id}", response_model=OfficialDocument)
async def read_section(
    section_id: str,
    response: Response,
    user_id: Annotated[UUID, Depends(get_learning_user_id)],
    service: Annotated[LearningService, Depends(get_learning_service)],
) -> OfficialDocument:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.read_document(section_id)
    except LearningSourceError as exc:
        raise source_http_error(exc) from None


@router.get("/state", response_model=LearningState)
async def read_state(
    response: Response,
    user_id: Annotated[UUID, Depends(get_learning_user_id)],
    service: Annotated[LearningService, Depends(get_learning_service)],
    source: Literal["demo", "notion"] = "demo",
) -> LearningState:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.read_state(user_id=user_id, source=source)
    except LearningSourceError as exc:
        raise source_http_error(exc) from None
