from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.models import Message
from ai_workspace.repositories import MessageRepository


class InvalidMessageRoleError(Exception):
    """The message role is not supported."""


class MessageService:
    """Persist messages after the caller checks conversation ownership."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = MessageRepository(session)

    async def create(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
    ) -> Message:
        if role not in {"user", "assistant"}:
            raise InvalidMessageRoleError("Invalid message role")

        try:
            message = await self.repository.create(
                conversation_id=conversation_id, role=role, content=content
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return message

    async def list_by_conversation(self, conversation_id: UUID) -> list[Message]:
        return await self.repository.list_by_conversation(conversation_id)
