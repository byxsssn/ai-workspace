from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.models import Conversation, Message
from ai_workspace.repositories import ConversationRepository, MessageRepository


class ConversationNotFoundError(Exception):
    """The conversation is unavailable to the specified user."""


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = ConversationRepository(session)

    async def create(
        self,
        user_id: UUID,
        title: str = "New conversation",
    ) -> Conversation:
        normalized_title = title.strip() or "New conversation"
        try:
            conversation = await self.repository.create(
                user_id=user_id, title=normalized_title
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return conversation

    async def get(self, conversation_id: UUID, user_id: UUID) -> Conversation:
        conversation = await self.repository.get_by_id_for_user(
            conversation_id, user_id
        )
        if conversation is None:
            raise ConversationNotFoundError("Conversation not found")
        return conversation

    async def list_for_user(self, user_id: UUID) -> list[Conversation]:
        return await self.repository.list_by_user(user_id)

    async def list_messages(
        self, conversation_id: UUID, user_id: UUID
    ) -> list[Message]:
        # Check ownership before querying messages; foreign and missing IDs are 404.
        await self.get(conversation_id, user_id)
        return await MessageRepository(self.session).list_by_conversation(
            conversation_id
        )
