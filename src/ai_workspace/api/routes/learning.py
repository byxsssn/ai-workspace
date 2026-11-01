from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from ai_workspace.api.dependencies.learning import (
    get_learning_service,
    get_learning_user_id,
)
from ai_workspace.api.errors import source_http_error
from ai_workspace.learning.documents import SECTIONS
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.models import (
    DocumentSection,
    LearningState,
    OfficialDocument,
)
from ai_workspace.learning.service import LearningService

router = APIRouter(prefix="/learning/fastapi", tags=["learning"])


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
