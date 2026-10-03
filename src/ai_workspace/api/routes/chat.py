from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.db.session import get_db_session
from ai_workspace.models import User
from ai_workspace.providers import ProviderError
from ai_workspace.providers.openrouter import OpenRouterProvider
from ai_workspace.schemas import ChatRequest, ChatResponse
from ai_workspace.services import (
    ChatService,
    ConversationNotFoundError,
    ProviderCredentialNotFoundError,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("/{conversation_id}/chat", response_model=ChatResponse)
async def chat_completion(
    conversation_id: UUID,
    request: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChatResponse:
    try:
        result = await ChatService(session, OpenRouterProvider()).complete_turn(
            user_id=current_user.id,
            conversation_id=conversation_id,
            model=request.model,
            content=request.content,
            reasoning_effort=request.reasoning_effort,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        ) from exc
    except ProviderCredentialNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider credential not configured",
        ) from exc
    except ProviderError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Chat provider request failed",
        ) from None

    return ChatResponse.model_validate(result)
