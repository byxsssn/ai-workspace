from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.learning.agent_models import LearningChatResponse
from ai_workspace.learning.chat import prepare_learning_messages
from ai_workspace.providers import (
    ModelMessage,
    ModelResponse,
    ProviderId,
    ReasoningEffort,
)
from ai_workspace.services.turn import (
    ConversationContext,
    ConversationTurnService,
    HistorySnapshot,
)

if TYPE_CHECKING:
    from ai_workspace.agents.runner import AgentRunner


class ChatService:
    """Prepare conversation context, then execute every turn with one AgentRunner."""

    def __init__(self, session: AsyncSession, agent: AgentRunner) -> None:
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
        reasoning_effort: ReasoningEffort | None = None,
        requested_context: ConversationContext | None = None,
    ) -> ModelResponse | LearningChatResponse:
        async def generate(
            api_key: str,
            history: HistorySnapshot,
            context: ConversationContext,
        ) -> ModelResponse | LearningChatResponse:
            source = context.learning_source
            if source is not None:
                messages = prepare_learning_messages(
                    model=model,
                    content=content,
                    history=history,
                    source=source,
                    reasoning_effort=reasoning_effort,
                )
            else:
                messages = [
                    ModelMessage(role=role, content=text) for _, role, text in history
                ]
                messages.append(ModelMessage(role="user", content=content))
            return await self.agent.run(
                user_id=user_id,
                api_key=api_key,
                model=model,
                messages=messages,
                source=source,
                reasoning_effort=reasoning_effort,
            )

        return await self.turn.complete_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            content=content,
            provider_id=ProviderId.OPENROUTER,
            generate=generate,
            requested_context=requested_context,
        )
