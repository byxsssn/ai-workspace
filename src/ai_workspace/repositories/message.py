from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.models import Message


class MessageRepository:
    """Access messages within a caller-managed transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
    ) -> Message:
        # PostgreSQL now() is fixed per transaction; chat messages need write order.
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            created_at=func.clock_timestamp(),
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_by_conversation(self, conversation_id: UUID) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        return list(result.scalars().all())
