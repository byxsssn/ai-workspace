from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.db.session import get_db_session
from ai_workspace.main import app
from ai_workspace.models import ProviderCredential, User
from ai_workspace.services import (
    ProviderCredentialNotFoundError,
    ProviderCredentialService,
)


@pytest.fixture
def client_user_service(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, User, Mock]]:
    user = User(id=uuid4())
    session = AsyncMock(spec=AsyncSession)
    service = Mock(spec=ProviderCredentialService)
    monkeypatch.setattr(
        "ai_workspace.api.routes.providers.ProviderCredentialService",
        Mock(return_value=service),
    )

    async def override_current_user() -> User:
        return user

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield session

    overrides = {
        get_current_user: override_current_user,
        get_db_session: override_db_session,
    }
    missing_override = object()
    previous_overrides = {
        dependency: app.dependency_overrides.get(dependency, missing_override)
        for dependency in overrides
    }
    app.dependency_overrides.update(overrides)
    try:
        with TestClient(app) as client:
            yield client, user, service
    finally:
        for dependency, previous_override in previous_overrides.items():
            if previous_override is missing_override:
                app.dependency_overrides.pop(dependency, None)
            else:
                app.dependency_overrides[dependency] = previous_override


def make_credential(user_id: UUID, base_url: str | None = None) -> ProviderCredential:
    return ProviderCredential(
        id=uuid4(),
        user_id=user_id,
        provider="openrouter",
        encrypted_api_key="test-ciphertext-not-for-response",
        base_url=base_url,
        created_at=datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 9, 26, 13, 0, tzinfo=UTC),
    )


def test_save_openrouter_preserves_key_and_returns_only_metadata(
    client_user_service: tuple[TestClient, User, Mock],
) -> None:
    client, user, service = client_user_service
    api_key = "  sk-or-test-only-key\n "
    base_url = "https://gateway.example.test/v1"
    credential = make_credential(user.id, base_url)
    service.save_openrouter.return_value = credential

    response = client.put(
        "/providers/openrouter", json={"api_key": api_key, "base_url": base_url}
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider": "openrouter",
        "base_url": base_url,
        "configured": True,
        "created_at": "2026-09-26T12:00:00Z",
        "updated_at": "2026-09-26T13:00:00Z",
    }
    assert api_key.strip() not in response.text
    assert credential.encrypted_api_key not in response.text
    service.save_openrouter.assert_awaited_with(user.id, api_key, base_url)


def test_get_openrouter_returns_only_metadata(
    client_user_service: tuple[TestClient, User, Mock],
) -> None:
    client, user, service = client_user_service
    credential = make_credential(user.id)
    service.get_openrouter.return_value = credential

    response = client.get("/providers/openrouter")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "openrouter",
        "base_url": None,
        "configured": True,
        "created_at": "2026-09-26T12:00:00Z",
        "updated_at": "2026-09-26T13:00:00Z",
    }
    assert credential.encrypted_api_key not in response.text
    service.get_openrouter.assert_awaited_with(user.id)


def test_delete_openrouter_returns_empty_204(
    client_user_service: tuple[TestClient, User, Mock],
) -> None:
    client, user, service = client_user_service
    service.delete_openrouter.return_value = None

    response = client.delete("/providers/openrouter")

    assert response.status_code == 204
    assert response.content == b""
    service.delete_openrouter.assert_awaited_with(user.id)


def test_openrouter_failures_do_not_disclose_credentials(
    client_user_service: tuple[TestClient, User, Mock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, user, service = client_user_service
    for request, service_method in (
        (client.get, service.get_openrouter),
        (client.delete, service.delete_openrouter),
    ):
        service_method.side_effect = ProviderCredentialNotFoundError(
            "Private credential lookup details"
        )

        response = request("/providers/openrouter")

        assert response.status_code == 404
        assert response.json() == {"detail": "Provider credential not found"}
        service_method.assert_awaited_with(user.id)

    sensitive_marker = "test-key-must-not-appear-in-validation-errors"
    for payload in (
        {"api_key": sensitive_marker, "user_id": str(uuid4())},
        {"api_key": sensitive_marker, "provider": "another-provider"},
        {"api_key": sensitive_marker + "x" * 4096},
        {"api_key": sensitive_marker, "base_url": "x" * 2049},
    ):
        response = client.put("/providers/openrouter", json=payload)

        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid provider credential request"}
        assert sensitive_marker not in response.text
    service.save_openrouter.assert_not_called()

    service.reset_mock()
    with monkeypatch.context() as auth_override:
        auth_override.delitem(app.dependency_overrides, get_current_user)
        response = client.get("/providers/openrouter")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    service.get_openrouter.assert_not_called()
