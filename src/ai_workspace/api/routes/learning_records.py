from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from ai_workspace.api.dependencies.learning import (
    get_learning_service,
    get_learning_user_id,
)
from ai_workspace.api.errors import source_http_error
from ai_workspace.learning.catalog_models import (
    LearningRecordDetail,
    LearningRecordList,
    LearningRecordSource,
)
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.service import LearningService

router = APIRouter(prefix="/learning/records", tags=["learning"])


@router.get("", response_model=LearningRecordList)
async def list_records(
    response: Response,
    user_id: Annotated[UUID, Depends(get_learning_user_id)],
    service: Annotated[LearningService, Depends(get_learning_service)],
    source: LearningRecordSource = "notion",
) -> LearningRecordList:
    """只读地图摘要；笔记正文仅在详情接口读取。"""
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.list_records(user_id=user_id, source=source)
    except LearningSourceError as exc:
        raise source_http_error(exc) from None


@router.get("/{record_id}", response_model=LearningRecordDetail)
async def read_record(
    record_id: UUID,
    response: Response,
    user_id: Annotated[UUID, Depends(get_learning_user_id)],
    service: Annotated[LearningService, Depends(get_learning_service)],
    source: LearningRecordSource = "notion",
) -> LearningRecordDetail:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.read_record(
            user_id=user_id, source=source, record_id=record_id
        )
    except LearningSourceError as exc:
        raise source_http_error(exc) from None
