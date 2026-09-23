"""Integration tests requiring configured PostgreSQL and applied migrations.

Each test rolls back its outer transaction; session savepoints prevent an
accidental repository commit from persisting test users or conversations.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_workspace.core.config import get_settings
from ai_workspace.models import Conversation, User
from ai_workspace.repositories import ConversationRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            ) as session:
                yield session
        finally:
            await transaction.rollback()


@pytest.fixture
async def owner(db_session: AsyncSession) -> User:
    user = User(
        email=f"conversation-owner-{uuid4().hex}@example.invalid",
        password_hash="already-hashed-test-password",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.parametrize(
    "title", [None, "  My FIRST conversation  "], ids=["default-title", "custom-title"]
)
async def test_create_conversation_with_defaults(
    db_session: AsyncSession, owner: User, title: str | None
) -> None:
    repository = ConversationRepository(db_session)
    if title is None:
        conversation = await repository.create(user_id=owner.id)
        expected_title = "New conversation"
    else:
        conversation = await repository.create(user_id=owner.id, title=title)
        expected_title = title

    assert isinstance(conversation, Conversation)
    assert isinstance(conversation.id, UUID)
    assert conversation.id.version == 4
    assert conversation.user_id == owner.id
    assert conversation.title == expected_title
    for timestamp in (conversation.created_at, conversation.updated_at):
        assert isinstance(timestamp, datetime)
        assert timestamp.utcoffset() is not None

    conversation_id = conversation.id
    db_session.expunge(conversation)
    stored_conversation = await db_session.get(Conversation, conversation_id)

    assert stored_conversation is not None
    assert stored_conversation is not conversation
    assert stored_conversation.user_id == owner.id
    assert stored_conversation.title == expected_title


async def test_get_conversation_by_id_only_for_its_owner(
    db_session: AsyncSession, owner: User
) -> None:
    other_user = User(
        email=f"conversation-other-{uuid4().hex}@example.invalid",
        password_hash="already-hashed-test-password",
    )
    db_session.add(other_user)
    await db_session.flush()
    conversation = Conversation(user_id=owner.id, title="An owner's conversation")
    db_session.add(conversation)
    await db_session.flush()
    conversation_id = conversation.id
    owner_id = owner.id
    other_user_id = other_user.id
    db_session.expunge_all()
    repository = ConversationRepository(db_session)

    loaded_conversation = await repository.get_by_id_for_user(conversation_id, owner_id)

    assert loaded_conversation is not None
    assert loaded_conversation is not conversation
    assert loaded_conversation.id == conversation_id
    assert loaded_conversation.user_id == owner_id
    assert await repository.get_by_id_for_user(conversation_id, other_user_id) is None
    assert await repository.get_by_id_for_user(uuid4(), owner_id) is None


async def test_list_conversations_filters_owner_and_sorts_newest_first(
    db_session: AsyncSession, owner: User
) -> None:
    other_user = User(
        email=f"conversation-other-{uuid4().hex}@example.invalid",
        password_hash="already-hashed-test-password",
    )
    db_session.add(other_user)
    await db_session.flush()
    owner_id = owner.id
    older_created_at = datetime(2026, 1, 1, tzinfo=UTC)
    newer_created_at = datetime(2026, 1, 2, tzinfo=UTC)
    shared_updated_at = datetime(2026, 1, 3, tzinfo=UTC)
    lower_id, higher_id = sorted([uuid4(), uuid4()])
    latest_updated = Conversation(
        user_id=owner_id,
        title="Latest update",
        created_at=older_created_at,
        updated_at=datetime(2026, 1, 4, tzinfo=UTC),
    )
    later_created = Conversation(
        user_id=owner_id,
        title="Later creation",
        created_at=newer_created_at,
        updated_at=shared_updated_at,
    )
    higher_uuid = Conversation(
        id=higher_id,
        user_id=owner_id,
        title="Higher UUID tie breaker",
        created_at=older_created_at,
        updated_at=shared_updated_at,
    )
    lower_uuid = Conversation(
        id=lower_id,
        user_id=owner_id,
        title="Lower UUID tie breaker",
        created_at=older_created_at,
        updated_at=shared_updated_at,
    )
    other_conversation = Conversation(
        user_id=other_user.id,
        title="Another user's newer conversation",
        created_at=older_created_at,
        updated_at=datetime(2026, 1, 5, tzinfo=UTC),
    )
    db_session.add_all(
        [lower_uuid, later_created, other_conversation, latest_updated, higher_uuid]
    )
    await db_session.flush()
    expected_ids = [latest_updated.id, later_created.id, higher_id, lower_id]
    db_session.expunge_all()

    conversations = await ConversationRepository(db_session).list_by_user(owner_id)

    assert isinstance(conversations, list)
    assert [conversation.id for conversation in conversations] == expected_ids
    assert all(conversation.user_id == owner_id for conversation in conversations)


async def test_list_returns_empty_for_user_without_conversations(
    db_session: AsyncSession, owner: User
) -> None:
    other_user = User(
        email=f"conversation-other-{uuid4().hex}@example.invalid",
        password_hash="already-hashed-test-password",
    )
    db_session.add(other_user)
    await db_session.flush()
    db_session.add(
        Conversation(user_id=other_user.id, title="Another user's conversation")
    )
    await db_session.flush()
    owner_id = owner.id
    db_session.expunge_all()

    conversations = await ConversationRepository(db_session).list_by_user(owner_id)

    assert conversations == []


async def test_create_conversation_does_not_commit(
    db_session: AsyncSession, db_engine: AsyncEngine, owner: User
) -> None:
    after_commit = Mock()
    event.listen(db_session.sync_session, "after_commit", after_commit)
    try:
        conversation = await ConversationRepository(db_session).create(user_id=owner.id)

        after_commit.assert_not_called()
        async with db_engine.connect() as observer:
            stored_id = await observer.scalar(
                select(Conversation.id).where(Conversation.id == conversation.id)
            )
            assert stored_id is None
    finally:
        event.remove(db_session.sync_session, "after_commit", after_commit)
