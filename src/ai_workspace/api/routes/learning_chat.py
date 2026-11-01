from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.api.routes.learning import get_learning_service, source_http_error
from ai_workspace.db.session import get_db_session
from ai_workspace.learning.agent import LearningAgent
from ai_workspace.learning.agent_errors import LearningAgentError
from ai_workspace.learning.agent_models import LearningChatRequest, LearningChatResponse
from ai_workspace.learning.chat import (
    LearningChatInputError,
    LearningChatService,
    LearningConversationChangedError,
)
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.service import LearningService
from ai_workspace.models import User
from ai_workspace.services import (
    ConversationNotFoundError,
    ProviderCredentialNotFoundError,
)
from ai_workspace.services.turn import ConversationContextConflictError

router = APIRouter(prefix="/learning/fastapi/conversations", tags=["learning"])


def get_learning_agent(
    sources: Annotated[LearningService, Depends(get_learning_service)],
) -> LearningAgent:
    return LearningAgent(sources)


@router.post("/{conversation_id}/chat", response_model=LearningChatResponse)
async def learning_chat(
    conversation_id: UUID,
    request: LearningChatRequest,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    agent: Annotated[LearningAgent, Depends(get_learning_agent)],
) -> LearningChatResponse:
    headers = {"Cache-Control": "no-store"}
    response.headers.update(headers)
    try:
        return await LearningChatService(session, agent).complete_turn(
            user_id=user.id,
            conversation_id=conversation_id,
            model=request.model,
            content=request.content,
            source=request.source,
        )
    except ConversationNotFoundError:
        raise HTTPException(404, "Conversation not found", headers=headers) from None
    except ProviderCredentialNotFoundError:
        raise HTTPException(
            400, "Provider credential not configured", headers=headers
        ) from None
    except LearningConversationChangedError:
        raise HTTPException(
            409,
            {
                "code": "learning_conversation_changed",
                "message": "Conversation changed during generation; reload its history",
            },
            headers=headers,
        ) from None
    except ConversationContextConflictError:
        raise HTTPException(
            409,
            {
                "code": "conversation_context_conflict",
                "message": "Conversation learning source cannot be changed",
            },
            headers=headers,
        ) from None
    except LearningChatInputError as exc:
        raise HTTPException(422, exc.errors, headers=headers) from None
    except LearningSourceError as exc:
        raise source_http_error(exc) from None
    except LearningAgentError as exc:
        raise HTTPException(
            exc.status_code,
            {"code": exc.code, "message": str(exc)},
            headers=headers,
        ) from None
