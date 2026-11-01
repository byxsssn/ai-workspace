from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.agents.runner import AgentRunner
from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.api.dependencies.chat import get_agent_runner
from ai_workspace.api.errors import CHAT_ERRORS, chat_http_error
from ai_workspace.db.session import get_db_session
from ai_workspace.learning.agent_models import LearningChatResponse
from ai_workspace.models import User
from ai_workspace.schemas import ChatRequest, ChatResponse
from ai_workspace.services import ChatService

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post(
    "/{conversation_id}/chat", response_model=LearningChatResponse | ChatResponse
)
async def chat_completion(
    conversation_id: UUID,
    request: ChatRequest,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    agent: Annotated[AgentRunner, Depends(get_agent_runner)],
) -> ChatResponse | LearningChatResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await ChatService(session, agent).complete_turn(
            user_id=current_user.id,
            conversation_id=conversation_id,
            model=request.model,
            content=request.content,
            reasoning_effort=request.reasoning_effort,
        )
    except CHAT_ERRORS as exc:
        raise chat_http_error(exc) from None
    if isinstance(result, LearningChatResponse):
        return result
    return ChatResponse.model_validate(result)
