"""Safe HTTP error contracts shared by chat and learning source routes."""

from fastapi import HTTPException

from ai_workspace.learning.agent_errors import LearningAgentError
from ai_workspace.learning.chat import LearningChatInputError
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.providers import ProviderError
from ai_workspace.services import (
    ConversationNotFoundError,
    ProviderCredentialNotFoundError,
)
from ai_workspace.services.turn import (
    ConversationChangedError,
    ConversationContextConflictError,
)

CHAT_ERRORS = (
    ConversationNotFoundError,
    ProviderCredentialNotFoundError,
    ConversationChangedError,
    ConversationContextConflictError,
    LearningChatInputError,
    LearningSourceError,
    LearningAgentError,
    ProviderError,
)


def source_http_error(error: LearningSourceError) -> HTTPException:
    headers = {"Cache-Control": "no-store"}
    if error.retry_after is not None:
        headers["Retry-After"] = error.retry_after
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": str(error)},
        headers=headers,
    )


def chat_http_error(
    error: Exception, *, legacy_learning: bool = False
) -> HTTPException:
    headers = {"Cache-Control": "no-store"}
    if isinstance(error, ConversationNotFoundError):
        return HTTPException(404, "Conversation not found", headers=headers)
    if isinstance(error, ProviderCredentialNotFoundError):
        return HTTPException(400, "Provider credential not configured", headers=headers)
    if isinstance(error, ConversationChangedError):
        return HTTPException(
            409,
            {
                "code": "learning_conversation_changed"
                if legacy_learning
                else "conversation_changed",
                "message": "Conversation changed during generation; reload its history",
            },
            headers=headers,
        )
    if isinstance(error, ConversationContextConflictError):
        return HTTPException(
            409,
            {
                "code": "conversation_context_conflict",
                "message": "Conversation learning source cannot be changed",
            },
            headers=headers,
        )
    if isinstance(error, LearningChatInputError):
        return HTTPException(422, error.errors, headers=headers)
    if isinstance(error, LearningSourceError):
        return source_http_error(error)
    if isinstance(error, LearningAgentError):
        return HTTPException(
            error.status_code,
            {"code": error.code, "message": str(error)},
            headers=headers,
        )
    return HTTPException(502, "Chat provider request failed", headers=headers)
