from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.api.routes.learning import source_http_error
from ai_workspace.api.routes.learning_chat import get_learning_agent
from ai_workspace.db.session import get_db_session
from ai_workspace.learning.agent import LearningAgent
from ai_workspace.learning.agent_errors import LearningAgentError
from ai_workspace.learning.agent_models import LearningChatResponse
from ai_workspace.learning.chat import LearningChatInputError
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.models import User
from ai_workspace.providers import ProviderError
from ai_workspace.providers.openrouter import OpenRouterProvider
from ai_workspace.schemas import ChatRequest, ChatResponse
from ai_workspace.services import (
    ChatService,
    ConversationNotFoundError,
    ProviderCredentialNotFoundError,
)
from ai_workspace.services.turn import ConversationChangedError

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
    agent: Annotated[LearningAgent, Depends(get_learning_agent)],
) -> ChatResponse | LearningChatResponse:
    headers = {"Cache-Control": "no-store"}
    response.headers.update(headers)
    try:
        result = await ChatService(
            session, OpenRouterProvider(), learning_agent=agent
        ).complete_turn(
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
            headers=headers,
        ) from exc
    except ProviderCredentialNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider credential not configured",
            headers=headers,
        ) from exc
    except ConversationChangedError:
        raise HTTPException(
            409,
            {
                "code": "conversation_changed",
                "message": "Conversation changed during generation; reload its history",
            },
            headers=headers,
        ) from None
    except LearningChatInputError as exc:
        raise HTTPException(422, exc.errors, headers=headers) from None
    except LearningSourceError as exc:
        raise source_http_error(exc) from None
    except LearningAgentError as exc:
        raise HTTPException(
            exc.status_code, {"code": exc.code, "message": str(exc)}, headers=headers
        ) from None
    except ProviderError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Chat provider request failed",
            headers=headers,
        ) from None

    if isinstance(result, LearningChatResponse):
        return result
    return ChatResponse.model_validate(result)
