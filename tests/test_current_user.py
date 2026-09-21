from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies import auth as auth_dependencies
from ai_workspace.core.tokens import InvalidAccessTokenError
from ai_workspace.db.session import get_db_session
from ai_workspace.main import app
from ai_workspace.models import User
from ai_workspace.repositories import UserRepository


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


@pytest.fixture
def active_user() -> User:
    return User(
        id=uuid4(),
        email="user@example.com",
        password_hash="a-stored-password-hash",
        is_active=True,
        created_at=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
    )


def test_current_user_returns_public_fields_for_valid_bearer_token(
    client_and_session: tuple[TestClient, AsyncMock],
    active_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session = client_and_session
    decode_token = Mock(return_value=active_user.id)
    monkeypatch.setattr(auth_dependencies, "decode_access_token", decode_token)
    repository = Mock(spec=UserRepository)
    repository.get_by_id.return_value = active_user
    repository_factory = Mock(return_value=repository)
    monkeypatch.setattr(auth_dependencies, "UserRepository", repository_factory)

    response = client.get(
        "/users/me", headers={"Authorization": "Bearer test-access-token"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "id": str(active_user.id),
        "email": active_user.email,
        "is_active": True,
        "created_at": "2026-09-21T12:00:00Z",
    }
    assert "password" not in data
    assert "password_hash" not in data
    decode_token.assert_called_once_with("test-access-token")
    repository_factory.assert_called_once_with(session)
    repository.get_by_id.assert_awaited_once_with(active_user.id)


@pytest.mark.parametrize(
    "authorization",
    [None, "Basic ignored-credentials", "Bearer", "Bearer "],
    ids=["missing-header", "wrong-scheme", "bare-bearer", "empty-bearer"],
)
def test_current_user_rejects_missing_bearer_token_before_decoding(
    client_and_session: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
    authorization: str | None,
) -> None:
    client, _ = client_and_session
    decode_token = Mock()
    monkeypatch.setattr(auth_dependencies, "decode_access_token", decode_token)
    repository_factory = Mock()
    monkeypatch.setattr(auth_dependencies, "UserRepository", repository_factory)
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.get("/users/me", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    decode_token.assert_not_called()
    repository_factory.assert_not_called()


@pytest.mark.parametrize(
    ("token", "error_message"),
    [
        ("malformed-token", "Private signature validation details"),
        ("expired-token", "Private expiration validation details"),
    ],
    ids=["malformed-token", "expired-token"],
)
def test_current_user_hides_token_errors_and_does_not_query_users(
    client_and_session: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
    token: str,
    error_message: str,
) -> None:
    client, _ = client_and_session
    decode_token = Mock(side_effect=InvalidAccessTokenError(error_message))
    monkeypatch.setattr(auth_dependencies, "decode_access_token", decode_token)
    repository_factory = Mock()
    monkeypatch.setattr(auth_dependencies, "UserRepository", repository_factory)

    response = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert error_message not in response.text
    decode_token.assert_called_once_with(token)
    repository_factory.assert_not_called()


def test_current_user_rejects_token_for_missing_user(
    client_and_session: tuple[TestClient, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, session = client_and_session
    user_id = uuid4()
    decode_token = Mock(return_value=user_id)
    monkeypatch.setattr(auth_dependencies, "decode_access_token", decode_token)
    repository = Mock(spec=UserRepository)
    repository.get_by_id.return_value = None
    repository_factory = Mock(return_value=repository)
    monkeypatch.setattr(auth_dependencies, "UserRepository", repository_factory)

    response = client.get(
        "/users/me", headers={"Authorization": "Bearer test-access-token"}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    decode_token.assert_called_once_with("test-access-token")
    repository_factory.assert_called_once_with(session)
    repository.get_by_id.assert_awaited_once_with(user_id)


def test_current_user_rejects_inactive_user(
    client_and_session: tuple[TestClient, AsyncMock],
    active_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session = client_and_session
    active_user.is_active = False
    decode_token = Mock(return_value=active_user.id)
    monkeypatch.setattr(auth_dependencies, "decode_access_token", decode_token)
    repository = Mock(spec=UserRepository)
    repository.get_by_id.return_value = active_user
    repository_factory = Mock(return_value=repository)
    monkeypatch.setattr(auth_dependencies, "UserRepository", repository_factory)

    response = client.get(
        "/users/me", headers={"Authorization": "Bearer test-access-token"}
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "User is inactive"}
    assert "WWW-Authenticate" not in response.headers
    decode_token.assert_called_once_with("test-access-token")
    repository_factory.assert_called_once_with(session)
    repository.get_by_id.assert_awaited_once_with(active_user.id)


def test_current_user_route_uses_injected_user_without_authenticating_again(
    client_and_session: tuple[TestClient, AsyncMock],
    active_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session = client_and_session
    decode_token = Mock()
    monkeypatch.setattr(auth_dependencies, "decode_access_token", decode_token)
    repository_factory = Mock()
    monkeypatch.setattr(auth_dependencies, "UserRepository", repository_factory)

    async def override_get_current_user() -> User:
        return active_user

    dependency = auth_dependencies.get_current_user
    missing_override = object()
    previous_override = app.dependency_overrides.get(dependency, missing_override)
    app.dependency_overrides[dependency] = override_get_current_user
    try:
        response = client.get("/users/me")
    finally:
        if previous_override is missing_override:
            app.dependency_overrides.pop(dependency, None)
        else:
            app.dependency_overrides[dependency] = previous_override

    assert response.status_code == 200
    assert response.json()["id"] == str(active_user.id)
    decode_token.assert_not_called()
    repository_factory.assert_not_called()
    session.execute.assert_not_called()


def test_openapi_uses_http_bearer_and_json_login() -> None:
    schema = app.openapi()

    assert schema["paths"]["/users/me"]["get"]["security"] == [{"HTTPBearer": []}]
    security_scheme = schema["components"]["securitySchemes"]["HTTPBearer"]
    assert security_scheme["type"] == "http"
    assert security_scheme["scheme"] == "bearer"
    assert "flows" not in security_scheme
    assert "tokenUrl" not in security_scheme
    login_content = schema["paths"]["/auth/login"]["post"]["requestBody"]["content"]
    assert set(login_content) == {"application/json"}
