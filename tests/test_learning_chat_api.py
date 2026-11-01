from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.api.routes import learning_chat
from ai_workspace.db.session import get_db_session
from ai_workspace.learning.agent_errors import LearningAgentError
from ai_workspace.learning.agent_models import LearningChatResponse
from ai_workspace.learning.chat import LearningConversationChangedError
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.main import create_api_app
from ai_workspace.models import User
from ai_workspace.services import (
    ConversationNotFoundError,
    ProviderCredentialNotFoundError,
)
from ai_workspace.services.turn import ConversationContextConflictError


@pytest.fixture
def client_service(monkeypatch):
    app = create_api_app()
    user = User(id=uuid4())
    session = AsyncMock(spec=AsyncSession)
    agent = Mock()
    service = Mock()
    service.complete_turn = AsyncMock(
        return_value=LearningChatResponse(
            content="演示模式：请选择一个测试章节。",
            model="test/model",
            source="demo",
            status="needs_clarification",
            sources=[],
            tool_calls=[],
        )
    )
    factory = Mock(return_value=service)
    monkeypatch.setattr(learning_chat, "LearningChatService", factory)

    async def current_user():
        return user

    async def db_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db_session] = db_session
    app.dependency_overrides[learning_chat.get_learning_agent] = lambda: agent
    with TestClient(app) as client:
        yield client, user, session, agent, service, factory, app


def endpoint(conversation_id):
    return f"/learning/fastapi/conversations/{conversation_id}/chat"


def test_uses_authenticated_identity_and_requested_source(client_service):
    client, user, session, agent, service, factory, _ = client_service
    conversation_id = uuid4()
    response = client.post(
        endpoint(conversation_id),
        params={"user_id": str(uuid4())},
        json={"model": "test/model", "content": "继续学习", "source": "notion"},
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    factory.assert_called_once_with(session, agent)
    service.complete_turn.assert_awaited_once_with(
        user_id=user.id,
        conversation_id=conversation_id,
        model="test/model",
        content="继续学习",
        source="notion",
    )


@pytest.mark.parametrize(
    "body",
    [
        {"model": "test/model", "content": " "},
        {"model": "test/model", "content": "x" * 8001},
        {"model": "test/model", "content": "x", "source": "private"},
        {"model": "test/model", "content": "x", "user_id": "someone-else"},
        {"model": "test/model", "content": "x", "page_id": "someone-else"},
        {"model": "test/model", "content": "x", "api_key": "do-not-echo"},
        {"model": "bad model", "content": "x"},
    ],
)
def test_rejects_invalid_input_before_generation_without_echo(client_service, body):
    client, _, _, _, service, _, _ = client_service
    response = client.post(endpoint(uuid4()), json=body)
    assert response.status_code == 422
    assert "do-not-echo" not in response.text
    service.complete_turn.assert_not_called()


@pytest.mark.parametrize(
    "error,status,code",
    [
        (ConversationNotFoundError("private detail"), 404, None),
        (ProviderCredentialNotFoundError("private detail"), 400, None),
        (
            LearningConversationChangedError("private detail"),
            409,
            "learning_conversation_changed",
        ),
        (
            ConversationContextConflictError("private detail"),
            409,
            "conversation_context_conflict",
        ),
        (
            LearningSourceError("notion_not_configured", status_code=503),
            503,
            "notion_not_configured",
        ),
        (LearningAgentError(), 502, "learning_agent_failed"),
        (LearningAgentError("learning_agent_timeout"), 504, "learning_agent_timeout"),
    ],
)
def test_errors_are_controlled_and_not_cached(client_service, error, status, code):
    client, _, _, _, service, _, _ = client_service
    service.complete_turn.side_effect = error
    response = client.post(
        endpoint(uuid4()), json={"model": "test/model", "content": "继续学习"}
    )
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    assert "private detail" not in response.text
    if code:
        assert response.json()["detail"]["code"] == code


def test_login_is_required(client_service):
    client, _, _, _, service, _, app = client_service
    del app.dependency_overrides[get_current_user]
    response = client.post(
        endpoint(uuid4()), json={"model": "test/model", "content": "继续学习"}
    )
    assert response.status_code == 401
    service.complete_turn.assert_not_called()
