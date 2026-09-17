"""Integration tests requiring configured PostgreSQL and applied migrations.

Each test uses an outer transaction that is always rolled back; repository
sessions use savepoints so an accidental commit cannot persist test rows.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_workspace.core.config import get_settings
from ai_workspace.models import User
from ai_workspace.repositories import UserRepository

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


async def test_create_user_with_defaults(db_session: AsyncSession) -> None:
    email = f"Repository-{uuid4().hex}@Example.invalid"
    password_hash = "already-hashed-test-password"

    user = await UserRepository(db_session).create(
        email=email, password_hash=password_hash
    )

    assert isinstance(user, User)
    assert isinstance(user.id, UUID)
    assert user.id.version == 4
    assert user.email == email
    assert user.password_hash == password_hash
    assert user.is_active is True
    for timestamp in (user.created_at, user.updated_at):
        assert isinstance(timestamp, datetime)
        assert timestamp.utcoffset() is not None
    stored_id = await db_session.scalar(select(User.id).where(User.id == user.id))
    assert stored_id == user.id


async def test_get_user_by_id(db_session: AsyncSession) -> None:
    email = f"repository-{uuid4().hex}@example.invalid"
    user = User(email=email, password_hash="already-hashed-test-password")
    db_session.add(user)
    await db_session.flush()
    user_id = user.id
    db_session.expunge(user)

    loaded_user = await UserRepository(db_session).get_by_id(user_id)

    assert loaded_user is not None
    assert loaded_user is not user
    assert loaded_user.id == user_id
    assert loaded_user.email == email


async def test_get_user_by_email(db_session: AsyncSession) -> None:
    email = f"Repository-{uuid4().hex}@Example.invalid"
    user = User(email=email, password_hash="already-hashed-test-password")
    db_session.add(user)
    await db_session.flush()
    user_id = user.id
    db_session.expunge(user)

    loaded_user = await UserRepository(db_session).get_by_email(email)

    assert loaded_user is not None
    assert loaded_user is not user
    assert loaded_user.id == user_id
    assert loaded_user.email == email


async def test_missing_users_return_none(db_session: AsyncSession) -> None:
    repository = UserRepository(db_session)
    missing_email = f"missing-{uuid4().hex}@example.invalid"

    assert await repository.get_by_id(uuid4()) is None
    assert await repository.get_by_email(missing_email) is None


async def test_create_user_does_not_commit(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    after_commit = Mock()
    event.listen(db_session.sync_session, "after_commit", after_commit)
    try:
        user = await UserRepository(db_session).create(
            email=f"repository-{uuid4().hex}@example.invalid",
            password_hash="already-hashed-test-password",
        )

        after_commit.assert_not_called()
        async with db_engine.connect() as observer:
            stored_id = await observer.scalar(select(User.id).where(User.id == user.id))
            assert stored_id is None
    finally:
        event.remove(db_session.sync_session, "after_commit", after_commit)
