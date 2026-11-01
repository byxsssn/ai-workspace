from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.api.dependencies.learning import get_learning_service
from ai_workspace.db.session import get_db_session
from ai_workspace.learning.demo import demo_learning_state
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.service import LearningService
from ai_workspace.main import create_api_app
from ai_workspace.models import User


@pytest.fixture
def learning_client():
    app = create_api_app()
    user = User(id=uuid4())
    session = AsyncMock(spec=AsyncSession)
    service = AsyncMock(spec=LearningService)

    async def current_user():
        return user

    async def db_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db_session] = db_session
    app.dependency_overrides[get_learning_service] = lambda: service
    with TestClient(app) as client:
        yield client, user, session, service, app


def test_sections_have_only_three_fixed_official_urls(learning_client):
    client, _, session, _, _ = learning_client
    response = client.get("/learning/fastapi/sections")
    assert response.status_code == 200
    assert {item["id"] for item in response.json()} == {
        "testing",
        "async-tests",
        "testing-dependencies",
    }
    assert all(
        item["url"].startswith("https://fastapi.tiangolo.com/")
        for item in response.json()
    )
    session.rollback.assert_awaited_once()


def test_state_releases_auth_transaction_and_uses_authenticated_owner(learning_client):
    client, user, session, service, _ = learning_client

    async def read_state(**kwargs):
        session.rollback.assert_awaited_once()
        assert kwargs == {"user_id": user.id, "source": "demo"}
        return demo_learning_state()

    service.read_state.side_effect = read_state
    response = client.get("/learning/fastapi/state", params={"user_id": str(uuid4())})
    assert response.status_code == 200
    assert response.json()["source"] == "demo"
    assert response.headers["Cache-Control"] == "no-store"


def test_notion_source_is_explicit(learning_client):
    client, user, _, service, _ = learning_client
    service.read_state.side_effect = LearningSourceError(
        "notion_not_configured", status_code=503
    )
    response = client.get("/learning/fastapi/state?source=notion")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "notion_not_configured"
    service.read_state.assert_awaited_once_with(user_id=user.id, source="notion")


def test_invalid_source_does_not_reach_reader(learning_client):
    client, _, _, service, _ = learning_client
    assert client.get("/learning/fastapi/state?source=someone-else").status_code == 422
    service.read_state.assert_not_called()


def test_document_error_is_sanitized_and_preserves_retry_after(learning_client):
    client, _, _, service, _ = learning_client
    service.read_document.side_effect = LearningSourceError(
        "source_rate_limited", status_code=503, retry_after="5"
    )
    response = client.get("/learning/fastapi/sections/testing")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "path", ["sections", "sections/testing", "state?source=notion"]
)
def test_learning_endpoints_require_authentication(learning_client, path):
    client, _, _, service, app = learning_client
    del app.dependency_overrides[get_current_user]
    response = client.get(f"/learning/fastapi/{path}")
    assert response.status_code == 401
    service.read_state.assert_not_called()
    service.read_document.assert_not_called()
