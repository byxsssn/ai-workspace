from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.core.encryption import decrypt_api_key
from ai_workspace.providers import (
    ChatCompletionResponse,
    ChatMessage,
    OpenRouterProvider,
)
from ai_workspace.repositories import MessageRepository
from ai_workspace.services.conversation import ConversationService
from ai_workspace.services.provider_credential import ProviderCredentialService


class ChatService:
    """Complete one chat and commit its two messages as a single transaction.

    The caller owns the session and provider lifecycle. The read transaction stays
    open during the provider call, but no messages are written until it succeeds.
    """

    def __init__(self, session: AsyncSession, provider: OpenRouterProvider) -> None:
        self.session = session
        self.provider = provider
        self.conversation_service = ConversationService(session)
        self.credential_service = ProviderCredentialService(session)
        self.message_repository = MessageRepository(session)

    async def chat_completion(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        model: str,
        content: str,
    ) -> ChatCompletionResponse:
        try:
            conversation = await self.conversation_service.get(conversation_id, user_id)
            credential = await self.credential_service.get_openrouter(user_id)
            api_key = decrypt_api_key(credential.encrypted_api_key)
            history = await self.message_repository.list_by_conversation(
                conversation_id
            )
            messages = [
                ChatMessage(role=message.role, content=message.content)
                for message in history
            ]
            messages.append(ChatMessage(role="user", content=content))
            response = await self.provider.chat_completion(
                api_key=api_key, model=model, messages=messages
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
