from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.db.session import get_db_session
from ai_workspace.main import app
from ai_workspace.models import User
from ai_workspace.services import EmailAlreadyRegisteredError, UserService


@pytest.fixture
def client_and_session() -> Iterator[tuple[TestClient, AsyncMock]]:
    session = AsyncMock(spec=AsyncSession)

    async def override_get_db_session() -> AsyncIterator[AsyncSession]:
        yield session

    missing_override = object()
    previous_override = app.dependency_overrides.get(get_db_session, missing_override)
    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        with TestClient(app) as client:
            yield client, session
    finally:
        if previous_override is missing_override:
            app.dependency_overrides.pop(get_db_session, None)
        else:
            app.dependency_overrides[get_db_session] = previous_override


@pytest.mark.parametrize(
    "password",
    ["a" * 8, "a" * 128, "  preserve password spaces  "],
    ids=["minimum-length", "maximum-length", "preserves-spaces"],
)
def test_register_returns_created_user_without_password_fields(
    client_and_session: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
    password: str,
) -> None:
    client, session = client_and_session
    user_id = uuid4()
    email = "user@example.com"
    user = User(
        id=user_id,
        email=email,
        is_active=True,
        created_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        password_hash="a-stored-password-hash",
    )
    service = Mock(spec=UserService)
    service.register.return_value = user
    service_factory = Mock(return_value=service)
    monkeypatch.setattr("ai_workspace.api.routes.users.UserService", service_factory)

    response = client.post(
        "/users/register", json={"email": email, "password": password}
    )

    assert response.status_code == 201
    data = response.json()
    assert data == {
        "id": str(user_id),
        "email": email,
        "is_active": True,
        "created_at": "2026-09-18T12:00:00Z",
    }
    assert "password" not in data
    assert "password_hash" not in data
    service_factory.assert_called_once_with(session)
    service.register.assert_awaited_once_with(email=email, password=password)


def test_register_rejects_invalid_email_without_calling_service(
    client_and_session: tuple[TestClient, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = client_and_session
    service_factory = Mock()
    monkeypatch.setattr("ai_workspace.api.routes.users.UserService", service_factory)

    response = client.post(
        "/users/register", json={"email": "not-an-email", "password": "password"}
    )

    assert response.status_code == 422
    assert any(error["loc"] == ["body", "email"] for error in response.json()["detail"])
    service_factory.assert_not_called()


@pytest.mark.parametrize(
    "password", ["a" * 7, "a" * 129], ids=["too-short", "too-long"]
)
def test_register_rejects_invalid_password_length_without_calling_service(
    client_and_session: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
    password: str,
) -> None:
    client, _ = client_and_session
    service_factory = Mock()
    monkeypatch.setattr("ai_workspace.api.routes.users.UserService", service_factory)

    response = client.post(
        "/users/register", json={"email": "user@example.com", "password": password}
    )

    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", "password"] for error in response.json()["detail"]
    )
    service_factory.assert_not_called()


def test_register_returns_conflict_for_duplicate_email(
    client_and_session: tuple[TestClient, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, session = client_and_session
    email = "existing@example.com"
    password = "password"
    service = Mock(spec=UserService)
    service.register.side_effect = EmailAlreadyRegisteredError()
    service_factory = Mock(return_value=service)
    monkeypatch.setattr("ai_workspace.api.routes.users.UserService", service_factory)

    response = client.post(
        "/users/register", json={"email": email, "password": password}
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Email is already registered"}
    service_factory.assert_called_once_with(session)
    service.register.assert_awaited_once_with(email=email, password=password)
