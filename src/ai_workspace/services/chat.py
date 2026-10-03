from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.core.encryption import decrypt_api_key
from ai_workspace.providers import (
    LLMProvider,
    ModelMessage,
    ModelResponse,
    ReasoningEffort,
)
from ai_workspace.repositories import MessageRepository
from ai_workspace.services.conversation import ConversationService
from ai_workspace.services.provider_credential import ProviderCredentialService


class ChatService:
    """Complete one chat and commit its two messages as a single transaction.

    The caller owns the session; the adapter owns its request resources. The read
    transaction stays open during generation, with no writes until it succeeds.
    """

    def __init__(self, session: AsyncSession, provider: LLMProvider) -> None:
        self.session = session
        self.provider = provider
        self.conversation_service = ConversationService(session)
        self.credential_service = ProviderCredentialService(session)
        self.message_repository = MessageRepository(session)

    async def complete_turn(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        model: str,
        content: str,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> ModelResponse:
        try:
            conversation = await self.conversation_service.get(conversation_id, user_id)
            credential = await self.credential_service.get(
                user_id, self.provider.provider_id
            )
            api_key = decrypt_api_key(credential.encrypted_api_key)
            history = await self.message_repository.list_by_conversation(
                conversation_id
            )
            messages = [
                ModelMessage(role=message.role, content=message.content)
                for message in history
            ]
            messages.append(ModelMessage(role="user", content=content))
            response = await self.provider.generate(
                api_key=api_key,
                model=model,
                messages=messages,
                reasoning_effort=reasoning_effort,
            )

            await self.message_repository.create(
                conversation_id=conversation_id, role="user", content=content
            )
            await self.message_repository.create(
                conversation_id=conversation_id,
                role="assistant",
                content=response.content,
            )
            # PostgreSQL now() would reflect transaction start, before the provider call.
            conversation.updated_at = datetime.now(UTC)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return response
