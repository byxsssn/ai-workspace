"""The single persistence boundary for both ordinary and learning chat turns."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.core.encryption import decrypt_api_key
from ai_workspace.models import Conversation, Message
from ai_workspace.providers import ProviderId
from ai_workspace.repositories import MessageRepository
from ai_workspace.services.conversation import (
    ConversationNotFoundError,
    ConversationService,
)
from ai_workspace.services.provider_credential import ProviderCredentialService

type HistorySnapshot = tuple[tuple[UUID, str, str], ...]


@dataclass(frozen=True)
class ConversationContext:
    learning_topic: Literal["fastapi"] | None = None
    learning_source: Literal["demo", "notion"] | None = None


class TurnResponse(Protocol):
    content: str


class ConversationChangedError(Exception):
    """History or execution context changed while a response was generated."""


class ConversationContextConflictError(Exception):
    """A compatibility request tried to change an already bound learning context."""


class ConversationTurnService:
    """Read, release the transaction, generate, then lock and atomically persist."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.conversation_service = ConversationService(session)
        self.credential_service = ProviderCredentialService(session)
        self.message_repository = MessageRepository(session)

    async def _read_history(self, conversation_id: UUID) -> HistorySnapshot:
        # Raw columns avoid stale ORM identity-cache values after concurrent writes.
        rows = await self.session.execute(
            select(Message.id, Message.role, Message.content)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        return tuple((row.id, row.role, row.content) for row in rows)

    async def complete_turn[ResponseT: TurnResponse](
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        content: str,
        provider_id: ProviderId,
        generate: Callable[
            [str, HistorySnapshot, ConversationContext], Awaitable[ResponseT]
        ],
        requested_context: ConversationContext | None = None,
    ) -> ResponseT:
        try:
            conversation = await self.conversation_service.get(conversation_id, user_id)
            original_context = ConversationContext(
                conversation.learning_topic, conversation.learning_source
            )
            if (
                requested_context is not None
                and original_context.learning_topic is not None
                and requested_context != original_context
            ):
                raise ConversationContextConflictError(
                    "Conversation learning context is fixed"
                )
            context = requested_context or original_context
            credential = await self.credential_service.get(user_id, provider_id)
            api_key = decrypt_api_key(credential.encrypted_api_key)
            snapshot = await self._read_history(conversation_id)
            await self.session.rollback()

            response = await generate(api_key, snapshot, context)

            result = await self.session.execute(
                select(Conversation)
                .where(
                    Conversation.id == conversation_id, Conversation.user_id == user_id
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            conversation = result.scalar_one_or_none()
            if conversation is None:
                raise ConversationNotFoundError("Conversation not found")
            current_context = ConversationContext(
                conversation.learning_topic, conversation.learning_source
            )
            if (
                current_context != original_context
                or await self._read_history(conversation_id) != snapshot
            ):
                raise ConversationChangedError("Conversation changed during generation")
            # A legacy learning request binds only after generation succeeds. The
            # row lock makes binding and the two messages a single atomic change.
            if context != original_context:
                conversation.learning_topic = context.learning_topic
                conversation.learning_source = context.learning_source
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
