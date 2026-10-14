import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace import production
from ai_workspace.core.config import Settings
from ai_workspace.db.session import get_db_session
from ai_workspace.models import User
from ai_workspace.services import UserService


@pytest.fixture
def production_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    static_dir = tmp_path / "dist"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<html>AI Workspace</html>")
    (static_dir / "assets" / "app-123.js").write_text("console.log('app')")
    (tmp_path / ".env").write_text("must-never-be-served")
    settings = Settings(
        _env_file=None,
        environment="production",
        debug=False,
        database_url="postgresql://test:test@localhost/test",
        jwt_secret_key="non-sensitive-test-jwt-secret-key-123456",
        credential_encryption_key=Fernet.generate_key().decode(),
        registration_invite_code="non-sensitive-test-invite-code-123456",
        frontend_dist=static_dir,
    )
    monkeypatch.setattr(production, "get_settings", lambda: settings)
    monkeypatch.setattr("ai_workspace.api.routes.users.get_settings", lambda: settings)
    return settings


def test_production_routes_headers_and_missing_resources(
    production_settings: Settings,
) -> None:
    with TestClient(production.create_app()) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert home.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in home.headers["content-security-policy"]
        asset = client.get("/assets/app-123.js")
        assert asset.status_code == 200
        assert "immutable" in asset.headers["cache-control"]
        assert client.get("/api/health").json() == {"status": "ok"}
        for path in (
            "/api/not-found",
            "/api/docs",
            "/api/redoc",
            "/api/openapi.json",
            "/docs",
            "/openapi.json",
            "/.env",
            "/assets/%2e%2e/.env",
        ):
            response = client.get(path)
            assert response.status_code == 404
            assert "must-never-be-served" not in response.text
            assert "<html>" not in response.text


def test_production_requires_frontend_build(production_settings: Settings) -> None:
    (production_settings.frontend_dist / "index.html").unlink()
    with pytest.raises(RuntimeError, match="Frontend build is missing"):
        production.create_app()


@pytest.mark.parametrize("available", [True, False])
def test_readiness_checks_database_and_hides_errors(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    probe = AsyncMock(
        side_effect=None if available else RuntimeError("private-database-url")
    )
    monkeypatch.setattr(production, "check_database", probe)
    with TestClient(production.create_app()) as client:
        response = client.get("/readyz")
    assert response.status_code == (200 if available else 503)
    assert response.json() == {"status": "ok" if available else "unavailable"}
    assert "private-database-url" not in response.text
    probe.assert_awaited_once()


def test_readiness_cancels_slow_database_probe(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancelled = []

    async def slow_probe():
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(production, "check_database", slow_probe)
    with TestClient(production.create_app()) as client:
        assert client.get("/readyz").status_code == 503
    assert cancelled == [True]


def test_invitation_is_checked_before_database_work_and_can_be_rotated(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = production.create_app()
    api = next(
        route.app for route in app.routes if getattr(route, "path", None) == "/api"
    )
    session = AsyncMock(spec=AsyncSession)

    async def session_override():
        yield session

    api.dependency_overrides[get_db_session] = session_override
    service = Mock(spec=UserService)
    service.register.return_value = User(
        id=uuid4(),
        email="invited@example.com",
        password_hash="hash",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    factory = Mock(return_value=service)
    monkeypatch.setattr("ai_workspace.api.routes.users.UserService", factory)
    body = {"email": "invited@example.com", "password": "a-valid-password"}
    code = production_settings.registration_invite_code.get_secret_value()
    with TestClient(app) as client:
        for supplied in (None, "", "wrong-code", "非ASCII邀请码"):
            payload = body | ({} if supplied is None else {"invite_code": supplied})
            result = client.post("/api/users/register", json=payload)
            assert result.status_code == 403
            assert result.json() == {"detail": "Invalid invitation code"}
        factory.assert_not_called()
        response = client.post("/api/users/register", json=body | {"invite_code": code})
        assert response.status_code == 201
        assert code not in response.text
        service.register.assert_awaited_once_with(**body)
        rotated = production_settings.model_copy(
            update={
                "registration_invite_code": type(
                    production_settings.registration_invite_code
                )("another-non-sensitive-invite-code-123456")
            }
        )
        monkeypatch.setattr(
            "ai_workspace.api.routes.users.get_settings", lambda: rotated
        )
        assert (
            client.post(
                "/api/users/register", json=body | {"invite_code": code}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/users/register",
                json=body
                | {"invite_code": rotated.registration_invite_code.get_secret_value()},
            ).status_code
            == 201
        )


@pytest.mark.parametrize("route", ["/api/users/register", "/api/auth/login"])
def test_validation_does_not_echo_secrets(
    production_settings: Settings, route: str, caplog: pytest.LogCaptureFixture
) -> None:
    password = "sensitive-password-" * 10
    invite = "sensitive-invitation-" * 20
    with TestClient(production.create_app()) as client:
        response = client.post(
            route,
            json={
                "email": "invalid-email",
                "password": password,
                "invite_code": invite,
            },
        )
        malformed = client.post(
            route,
            content='{"password":"raw-secret",',
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == malformed.status_code == 422
    for value in (password, invite, "raw-secret"):
        assert value not in response.text + malformed.text + caplog.text
