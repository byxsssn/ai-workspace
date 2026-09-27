"""Service persistence test; commits stay inside an outer rollback transaction."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_workspace.core import encryption
from ai_workspace.core.config import get_settings
from ai_workspace.models import ProviderCredential, User
from ai_workspace.services import (
    ProviderCredentialNotFoundError,
    ProviderCredentialService,
)

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


async def test_save_replace_and_delete_preserve_secrets_and_user_isolation(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    encryption_settings = SimpleNamespace(
        credential_encryption_key=SecretStr(Fernet.generate_key().decode("ascii"))
    )
    monkeypatch.setattr(encryption, "get_settings", lambda: encryption_settings)
    owner = User(
        email=f"credential-service-owner-{uuid4().hex}@example.invalid",
        password_hash="already-hashed-test-password",
    )
    other_user = User(
        email=f"credential-service-other-{uuid4().hex}@example.invalid",
        password_hash="already-hashed-test-password",
    )
    db_session.add_all([owner, other_user])
    await db_session.flush()
    owner_id, other_user_id = owner.id, other_user.id
    service = ProviderCredentialService(db_session)
    api_key = "  test-only-key-原文\n  "

    saved = await service.save_openrouter(owner_id, api_key)

    credential_id = saved.id
    stored_ciphertext = await db_session.scalar(
        select(ProviderCredential.encrypted_api_key).where(
            ProviderCredential.id == credential_id
        )
    )
    assert stored_ciphertext is not None
    assert stored_ciphertext != api_key
    assert encryption.decrypt_api_key(stored_ciphertext) == api_key
    other_saved = await service.save_openrouter(other_user_id, "other-user-test-key")
    other_id, other_ciphertext = other_saved.id, other_saved.encrypted_api_key

    # PostgreSQL now() is constant within the test's outer transaction.
    old_timestamp = datetime(2000, 1, 1, tzinfo=UTC)
    saved.updated_at = old_timestamp
    await db_session.flush()
    replacement = "\t replacement-test-key\n"

    updated = await service.save_openrouter(
        owner_id, replacement, base_url="https://openrouter.ai/api/v1"
    )

    assert updated.id == credential_id
    assert updated.updated_at > old_timestamp
    assert updated.updated_at.utcoffset() is not None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ProviderCredential)
            .where(
                ProviderCredential.user_id == owner_id,
                ProviderCredential.provider == "openrouter",
            )
        )
        == 1
    )
    db_session.expunge_all()
    persisted = await service.get_openrouter(owner_id)
    assert persisted.id == credential_id
    assert persisted.encrypted_api_key != replacement
    assert encryption.decrypt_api_key(persisted.encrypted_api_key) == replacement
    assert persisted.base_url == "https://openrouter.ai/api/v1"
    other_persisted = await service.get_openrouter(other_user_id)
    assert other_persisted.id == other_id
    assert other_persisted.encrypted_api_key == other_ciphertext

    await service.delete_openrouter(owner_id)

    with pytest.raises(ProviderCredentialNotFoundError):
        await service.get_openrouter(owner_id)
    remaining = await service.get_openrouter(other_user_id)
    assert remaining.id == other_id
    assert remaining.encrypted_api_key == other_ciphertext
