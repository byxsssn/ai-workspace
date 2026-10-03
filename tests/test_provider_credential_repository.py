"""PostgreSQL repository test with all rows isolated in a rolled-back transaction."""

from collections.abc import AsyncIterator
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_workspace.core.config import get_settings
from ai_workspace.models import User
from ai_workspace.repositories import ProviderCredentialRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
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
                    yield session
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_repository_crud_isolates_users_and_does_not_commit(
    db_session: AsyncSession,
) -> None:
    owner = User(
        email=f"credential-owner-{uuid4().hex}@example.invalid",
        password_hash="already-hashed-test-password",
    )
    other_user = User(
        email=f"credential-other-{uuid4().hex}@example.invalid",
        password_hash="already-hashed-test-password",
    )
    db_session.add_all([owner, other_user])
    await db_session.flush()
    owner_id, other_user_id = owner.id, other_user.id
    repository = ProviderCredentialRepository(db_session)
    after_commit = Mock()
    event.listen(db_session.sync_session, "after_commit", after_commit)
    try:
        credential = await repository.create(
            user_id=owner_id,
            provider="openrouter",
            encrypted_api_key="test-ciphertext-one",
        )
        other_credential = await repository.create(
            user_id=other_user_id,
            provider="openrouter",
            encrypted_api_key="other-user-test-ciphertext",
        )
        credential_id, other_id = credential.id, other_credential.id
        db_session.expunge_all()

        loaded = await repository.get_by_user_and_provider(owner_id, "openrouter")

        assert loaded is not None
        assert loaded is not credential
        assert loaded.id == credential_id
        assert loaded.user_id == owner_id
        assert loaded.encrypted_api_key == "test-ciphertext-one"
        assert loaded.base_url is None
        assert await repository.get_by_user_and_provider(uuid4(), "openrouter") is None
        assert await repository.get_by_user_and_provider(owner_id, "other") is None

        updated = await repository.update(
            loaded, encrypted_api_key="test-ciphertext-two"
        )

        assert updated.id == credential_id
        assert updated.encrypted_api_key == "test-ciphertext-two"
        assert updated.base_url is None
        assert updated.updated_at.utcoffset() is not None
        db_session.expunge_all()
        persisted = await repository.get_by_user_and_provider(owner_id, "openrouter")
        assert persisted is not None
        assert persisted.encrypted_api_key == "test-ciphertext-two"
        assert persisted.base_url is None

        await repository.delete(persisted)

        assert await repository.get_by_user_and_provider(owner_id, "openrouter") is None
        remaining = await repository.get_by_user_and_provider(
            other_user_id, "openrouter"
        )
        assert remaining is not None
        assert remaining.id == other_id
        assert remaining.encrypted_api_key == "other-user-test-ciphertext"
        after_commit.assert_not_called()
    finally:
        event.remove(db_session.sync_session, "after_commit", after_commit)
