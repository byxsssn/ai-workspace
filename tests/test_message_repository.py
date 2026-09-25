"""PostgreSQL integration test; all test rows are rolled back."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_workspace.core.config import get_settings
from ai_workspace.models import Conversation, Message, User
from ai_workspace.repositories import MessageRepository


def test_create_and_list_messages_preserve_content_order_and_transaction() -> None:
    async def run() -> None:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    async with AsyncSession(
                        bind=connection,
                        join_transaction_mode="create_savepoint",
                        expire_on_commit=False,
                    ) as session:
                        owner = User(
                            email=f"message-owner-{uuid4().hex}@example.invalid",
                            password_hash="already-hashed-test-password",
                        )
                        session.add(owner)
                        await session.flush()
                        conversation = Conversation(user_id=owner.id)
                        other_conversation = Conversation(user_id=owner.id)
                        session.add_all([conversation, other_conversation])
                        await session.flush()
                        conversation_id = conversation.id
                        after_commit = Mock()
                        event.listen(session.sync_session, "after_commit", after_commit)
                        try:
                            repository = MessageRepository(session)
                            content = "  First line\nSecond line  "
                            message = await repository.create(
                                conversation_id=conversation_id,
                                role="user",
                                content=content,
                            )

                            assert isinstance(message, Message)
                            assert isinstance(message.id, UUID)
                            assert message.id.version == 4
                            assert message.conversation_id == conversation_id
                            assert message.role == "user"
                            assert message.content == content
                            assert isinstance(message.created_at, datetime)
                            assert message.created_at.utcoffset() is not None
                            after_commit.assert_not_called()

                            earlier = message.created_at - timedelta(days=1)
                            lower_id, higher_id = sorted([uuid4(), uuid4()])
                            session.add_all(
                                [
                                    Message(
                                        id=higher_id,
                                        conversation_id=conversation_id,
                                        role="assistant",
                                        content="Higher UUID",
                                        created_at=earlier,
                                    ),
                                    Message(
                                        conversation_id=other_conversation.id,
                                        role="user",
                                        content="Another conversation",
                                        created_at=earlier - timedelta(days=1),
                                    ),
                                    Message(
                                        id=lower_id,
                                        conversation_id=conversation_id,
                                        role="user",
                                        content="Lower UUID",
                                        created_at=earlier,
                                    ),
                                ]
                            )
                            await session.flush()
                            expected_ids = [lower_id, higher_id, message.id]
                            session.expunge_all()

                            messages = await repository.list_by_conversation(
                                conversation_id
                            )

                            assert isinstance(messages, list)
                            assert [item.id for item in messages] == expected_ids
                            assert [item.content for item in messages] == [
                                "Lower UUID",
                                "Higher UUID",
                                content,
                            ]
                            assert all(
                                item.conversation_id == conversation_id
                                for item in messages
                            )
                            assert messages[-1] is not message
                            assert messages[-1].role == "user"
                            after_commit.assert_not_called()
                        finally:
                            event.remove(
                                session.sync_session, "after_commit", after_commit
                            )
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(run())
