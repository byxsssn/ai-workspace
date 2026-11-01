from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.agents.runner import AgentRunner
from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.api.dependencies.chat import get_agent_runner
from ai_workspace.api.errors import CHAT_ERRORS, chat_http_error
from ai_workspace.db.session import get_db_session
from ai_workspace.learning.agent_models import LearningChatRequest, LearningChatResponse
from ai_workspace.models import User
from ai_workspace.services import ChatService
from ai_workspace.services.turn import ConversationContext

router = APIRouter(prefix="/learning/fastapi/conversations", tags=["learning"])


@router.post("/{conversation_id}/chat", response_model=LearningChatResponse)
async def learning_chat(
    conversation_id: UUID,
    request: LearningChatRequest,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    agent: Annotated[AgentRunner, Depends(get_agent_runner)],
) -> LearningChatResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await ChatService(session, agent).complete_turn(
            user_id=user.id,
            conversation_id=conversation_id,
            model=request.model,
            content=request.content,
            requested_context=ConversationContext("fastapi", request.source),
        )
    except CHAT_ERRORS as exc:
        raise chat_http_error(exc, legacy_learning=True) from None
    return LearningChatResponse.model_validate(result)
