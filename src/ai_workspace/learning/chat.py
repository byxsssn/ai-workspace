import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.core.encryption import decrypt_api_key
from ai_workspace.learning.agent_models import LearningChatResponse
from ai_workspace.models import Conversation, Message
from ai_workspace.providers import ModelMessage, ProviderId
from ai_workspace.repositories import MessageRepository
from ai_workspace.services.conversation import (
    ConversationNotFoundError,
    ConversationService,
)
from ai_workspace.services.provider_credential import ProviderCredentialService

if TYPE_CHECKING:
    from ai_workspace.learning.agent import LearningAgent


_MAX_CONTEXT_MESSAGES = 20
_MAX_CONTEXT_CHARACTERS = 24_000
type HistorySnapshot = tuple[tuple[UUID, str, str], ...]


class LearningConversationChangedError(Exception):
    """生成期间会话历史已变化，当前答案不能再按旧上下文提交。"""


class LearningChatService:
    """读取、生成、保存分别执行，外部网络等待期间不占用数据库事务。"""

    def __init__(self, session: AsyncSession, agent: LearningAgent) -> None:
        self.session = session
        self.agent = agent
        self.conversation_service = ConversationService(session)
        self.credential_service = ProviderCredentialService(session)
        self.message_repository = MessageRepository(session)

    async def _read_history(self, conversation_id: UUID) -> HistorySnapshot:
        # 读取普通列，避免 ORM 身份缓存隐藏另一请求已经提交的消息变化。
        result = await self.session.execute(
            select(Message.id, Message.role, Message.content)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        return tuple((row.id, row.role, row.content) for row in result)

    async def complete_turn(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        model: str,
        content: str,
        source: Literal["demo", "notion"] = "demo",
    ) -> LearningChatResponse:
        try:
            await self.conversation_service.get(conversation_id, user_id)
            credential = await self.credential_service.get(
                user_id, ProviderId.OPENROUTER
            )
            api_key = decrypt_api_key(credential.encrypted_api_key)
            snapshot = await self._read_history(conversation_id)
            messages = _bounded_context(snapshot, content)
            # 必要的纯数据已复制；rollback 结束鉴权、凭据和历史的只读事务。
            await self.session.rollback()

            response = await self.agent.run(
                user_id=user_id,
                api_key=api_key,
                model=model,
                messages=messages,
                source=source,
            )

            result = await self.session.execute(
                select(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            conversation = result.scalar_one_or_none()
            if conversation is None:
                raise ConversationNotFoundError("Conversation not found")
            # 同一学习会话的写入在行锁内重新核对；不把过期答案追加进新历史。
            if await self._read_history(conversation_id) != snapshot:
                raise LearningConversationChangedError(
                    "Conversation changed during learning response"
                )
            await self.message_repository.create(
                conversation_id=conversation_id, role="user", content=content
            )
            await self.message_repository.create(
                conversation_id=conversation_id,
                role="assistant",
                content=response.content,
            )
            conversation.updated_at = datetime.now(UTC)
            await self.session.commit()
        except Exception, asyncio.CancelledError:
            await self.session.rollback()
            raise
        return response


def _bounded_context(snapshot: HistorySnapshot, content: str) -> list[ModelMessage]:
    """只保留近期完整文本消息；上限包含本轮问题，不截断单条历史。"""
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
