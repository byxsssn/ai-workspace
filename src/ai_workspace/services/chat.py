from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.learning.agent_models import LearningChatResponse
from ai_workspace.providers import (
    LLMProvider,
    ModelMessage,
    ModelResponse,
    ReasoningEffort,
)
from ai_workspace.services.turn import (
    ConversationContext,
    ConversationTurnService,
    HistorySnapshot,
)

if TYPE_CHECKING:
    from ai_workspace.learning.agent import LearningAgent


class ChatService:
    """Dispatch by persisted conversation context, sharing one turn lifecycle."""

    def __init__(
        self,
        session: AsyncSession,
        provider: LLMProvider,
        *,
        learning_agent: LearningAgent | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.learning_agent = learning_agent
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
        reasoning_effort: ReasoningEffort | None = None,
    ) -> ModelResponse | LearningChatResponse:
        async def generate(
            api_key: str,
            history: HistorySnapshot,
            context: ConversationContext,
        ) -> ModelResponse | LearningChatResponse:
            if context.learning_topic == "fastapi":
                from ai_workspace.learning.chat import generate_learning_response

                if self.learning_agent is None or context.learning_source is None:
                    raise RuntimeError("Learning agent is unavailable")
                return await generate_learning_response(
                    self.learning_agent,
                    user_id=user_id,
                    api_key=api_key,
                    model=model,
                    content=content,
                    history=history,
                    source=context.learning_source,
                    reasoning_effort=reasoning_effort,
                )
            messages = [
                ModelMessage(role=role, content=text) for _, role, text in history
            ]
            messages.append(ModelMessage(role="user", content=content))
            return await self.provider.generate(
                api_key=api_key,
                model=model,
                messages=messages,
                reasoning_effort=reasoning_effort,
            )

        return await self.turn.complete_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            content=content,
            provider_id=self.provider.provider_id,
            generate=generate,
        )
