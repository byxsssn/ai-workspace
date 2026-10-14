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
    """完成一轮聊天，并在同一个事务中提交用户和助手的两条消息。

    调用方管理数据库会话，适配器管理请求资源。
    生成期间保持读取事务，生成成功后才写入消息。
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
            # PostgreSQL 的 now() 对应事务开始时间，早于模型调用完成时间。
            conversation.updated_at = datetime.now(UTC)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return response
