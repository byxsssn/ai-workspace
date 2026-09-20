from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.db.session import get_db_session
from ai_workspace.main import app
from ai_workspace.models import User
from ai_workspace.services import (
    InactiveUserError,
    InvalidCredentialsError,
    UserService,
)


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
def test_login_returns_bearer_token_for_authenticated_user(
    client_and_session: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
    password: str,
) -> None:
    client, session = client_and_session
    email = "user@example.com"
    user = User(
        id=uuid4(),
        email=email,
        password_hash="a-stored-password-hash",
        is_active=True,
    )
    service = Mock(spec=UserService)
    service.authenticate.return_value = user
    service_factory = Mock(return_value=service)
    monkeypatch.setattr("ai_workspace.api.routes.auth.UserService", service_factory)
    create_token = Mock(return_value="fixed-access-token")
    monkeypatch.setattr(
        "ai_workspace.api.routes.auth.create_access_token", create_token
    )

    response = client.post("/auth/login", json={"email": email, "password": password})

    assert response.status_code == 200
    data = response.json()
    assert data == {"access_token": "fixed-access-token", "token_type": "bearer"}
    assert "password" not in data
    assert "password_hash" not in data
    assert "user" not in data
    service_factory.assert_called_once_with(session)
    service.authenticate.assert_awaited_once_with(email=email, password=password)
    create_token.assert_called_once_with(user.id)


@pytest.mark.parametrize(
    ("email", "password", "error_field"),
    [
        pytest.param("not-an-email", "password", "email", id="invalid-email"),
        pytest.param("user@example.com", "a" * 7, "password", id="short-password"),
        pytest.param("user@example.com", "a" * 129, "password", id="long-password"),
    ],
)
def test_login_rejects_invalid_input_before_authentication_or_token_creation(
    client_and_session: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
    email: str,
    password: str,
    error_field: str,
) -> None:
    client, _ = client_and_session
    service_factory = Mock()
    monkeypatch.setattr("ai_workspace.api.routes.auth.UserService", service_factory)
    create_token = Mock()
    monkeypatch.setattr(
        "ai_workspace.api.routes.auth.create_access_token", create_token
    )

    response = client.post("/auth/login", json={"email": email, "password": password})

    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", error_field] for error in response.json()["detail"]
    )
    service_factory.assert_not_called()
    create_token.assert_not_called()


@pytest.mark.parametrize(
    ("error_type", "expected_status", "expected_detail"),
    [
        pytest.param(
            InvalidCredentialsError,
            401,
            "Invalid email or password",
            id="invalid-credentials",
        ),
        pytest.param(InactiveUserError, 403, "User is inactive", id="inactive-user"),
    ],
)
def test_login_maps_authentication_errors_without_creating_token(
    client_and_session: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    expected_status: int,
    expected_detail: str,
) -> None:
    client, session = client_and_session
    email = "user@example.com"
    password = "password"
    service = Mock(spec=UserService)
    service.authenticate.side_effect = error_type(expected_detail)
    service_factory = Mock(return_value=service)
    monkeypatch.setattr("ai_workspace.api.routes.auth.UserService", service_factory)
    create_token = Mock()
    monkeypatch.setattr(
        "ai_workspace.api.routes.auth.create_access_token", create_token
    )

    response = client.post("/auth/login", json={"email": email, "password": password})

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    if expected_status == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"
    else:
        assert "WWW-Authenticate" not in response.headers
    service_factory.assert_called_once_with(session)
    service.authenticate.assert_awaited_once_with(email=email, password=password)
    create_token.assert_not_called()
