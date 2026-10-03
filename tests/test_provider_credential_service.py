import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.models import ProviderCredential
from ai_workspace.providers.types import ProviderId
from ai_workspace.repositories import ProviderCredentialRepository
from ai_workspace.services import (
    ProviderCredentialNotFoundError,
    ProviderCredentialService,
)


def test_write_failures_roll_back_and_reraise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ai_workspace.services.provider_credential.encrypt_api_key",
        Mock(return_value="test-ciphertext"),
    )
    for operation, failure_stage in (
        ("create", "write"),
        ("update", "write"),
        ("create", "commit"),
        ("delete", "write"),
        ("delete", "commit"),
    ):
        user_id = uuid4()
        session = AsyncMock(spec=AsyncSession)
        service = ProviderCredentialService(session)
        service.repository = Mock(spec=ProviderCredentialRepository)
        credential = ProviderCredential(user_id=user_id, provider="openrouter")
        service.repository.get_by_user_and_provider.return_value = (
            None if operation == "create" else credential
        )
        error = RuntimeError("Write failed")
        if failure_stage == "commit":
            session.commit.side_effect = error
        else:
            getattr(service.repository, operation).side_effect = error

        with pytest.raises(RuntimeError) as exc_info:
            if operation == "delete":
                asyncio.run(service.delete(user_id, ProviderId.OPENROUTER))
            else:
                asyncio.run(
                    service.save(user_id, ProviderId.OPENROUTER, "test-api-key")
                )

        assert exc_info.value is error
        session.rollback.assert_awaited()


def test_get_uses_user_scope_and_missing_credentials_raise_without_writes() -> None:
    user_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    service = ProviderCredentialService(session)
    service.repository = Mock(spec=ProviderCredentialRepository)
    credential = ProviderCredential(user_id=user_id, provider="openrouter")
    service.repository.get_by_user_and_provider.return_value = credential

    assert asyncio.run(service.get(user_id, ProviderId.OPENROUTER)) is credential
    service.repository.get_by_user_and_provider.assert_awaited_with(
        user_id, "openrouter"
    )

    service.repository.get_by_user_and_provider.return_value = None
    for method in (service.get, service.delete):
        with pytest.raises(ProviderCredentialNotFoundError):
            asyncio.run(method(user_id, ProviderId.OPENROUTER))

    service.repository.delete.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
