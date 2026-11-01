from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.learning.agent_models import LearningChatRequest, LearningChatResponse
from ai_workspace.providers import ModelMessage, ProviderId, ReasoningEffort
from ai_workspace.services.turn import (
    ConversationChangedError,
    ConversationContext,
    ConversationTurnService,
    HistorySnapshot,
)

if TYPE_CHECKING:
    from ai_workspace.learning.agent import LearningAgent

_MAX_CONTEXT_MESSAGES = 20
_MAX_CONTEXT_CHARACTERS = 24_000

# Preserve the old import and error mapping for the compatibility endpoint.
LearningConversationChangedError = ConversationChangedError


class LearningChatInputError(Exception):
    """Safe validation details for learning turns arriving at the shared endpoint."""

    def __init__(self, errors: list[dict[str, object]]) -> None:
        super().__init__("Invalid learning chat input")
        self.errors = errors


async def generate_learning_response(
    agent: LearningAgent,
    *,
    user_id: UUID,
    api_key: str,
    model: str,
    content: str,
    history: HistorySnapshot,
    source: Literal["demo", "notion"],
    reasoning_effort: ReasoningEffort | None = None,
) -> LearningChatResponse:
    try:
        request = LearningChatRequest(model=model, content=content, source=source)
    except ValidationError as exc:
        raise LearningChatInputError(
            [
                {
                    "loc": ["body", *error["loc"]],
                    "type": error["type"],
                    "msg": "Invalid value",
                }
                for error in exc.errors(include_input=False, include_context=False)
            ]
        ) from None
    if reasoning_effort is not None:
        raise LearningChatInputError(
            [
                {
                    "loc": ["body", "reasoning_effort"],
                    "type": "value_error",
                    "msg": "Learning chat currently uses the model's default reasoning effort",
                }
            ]
        )
    return await agent.run(
        user_id=user_id,
        api_key=api_key,
        model=request.model,
        messages=_bounded_context(history, request.content),
        source=request.source,
    )


class LearningChatService:
    """Compatibility adapter; the shared turn service owns all persistence."""

    def __init__(self, session: AsyncSession, agent: LearningAgent) -> None:
        self.session = session
        self.agent = agent
        self.turn = ConversationTurnService(session)
        self.conversation_service = self.turn.conversation_service
        self.credential_service = self.turn.credential_service
        self.message_repository = self.turn.message_repository

    async def complete_turn(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        model: str,
        content: str,
        source: Literal["demo", "notion"] = "demo",
    ) -> LearningChatResponse:
        async def generate(
            api_key: str,
            history: HistorySnapshot,
            context: ConversationContext,
        ) -> LearningChatResponse:
            return await generate_learning_response(
                self.agent,
                user_id=user_id,
                api_key=api_key,
                model=model,
                content=content,
                history=history,
                source=source,
            )

        return await self.turn.complete_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            content=content,
            provider_id=ProviderId.OPENROUTER,
            generate=generate,
            requested_context=ConversationContext("fastapi", source),
        )


def _bounded_context(snapshot: HistorySnapshot, content: str) -> list[ModelMessage]:
    """Keep recent whole messages, including this request in both limits."""
    if len(content) > _MAX_CONTEXT_CHARACTERS:
        raise ValueError("Learning input exceeds the context limit")
    history = [
        ModelMessage(role=role, content=text)
        for _, role, text in snapshot
        if role in ("user", "assistant")
    ][-(_MAX_CONTEXT_MESSAGES - 1) :]
    history.append(ModelMessage(role="user", content=content))
    characters = sum(len(message.content) for message in history)
    while characters > _MAX_CONTEXT_CHARACTERS:
        characters -= len(history.pop(0).content)
    while history[0].role == "assistant":
        history.pop(0)
    return history
