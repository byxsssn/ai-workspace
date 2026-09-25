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
from ai_workspace.models import Conversation, User
from ai_workspace.services import ConversationNotFoundError, ConversationService


@pytest.fixture
def client_user_service(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, User, Mock]]:
    user = User(id=uuid4())
    session = AsyncMock(spec=AsyncSession)
    service = Mock(spec=ConversationService)
    monkeypatch.setattr(
        "ai_workspace.api.routes.conversations.ConversationService",
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


def make_conversation(user_id: UUID, title: str) -> Conversation:
    return Conversation(
        id=uuid4(),
        user_id=user_id,
        title=title,
        created_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 9, 24, 13, 0, tzinfo=UTC),
    )


def test_create_conversation_returns_201(
    client_user_service: tuple[TestClient, User, Mock],
) -> None:
    client, user, service = client_user_service
    conversation = make_conversation(user.id, "Created conversation")
    service.create.return_value = conversation

    response = client.post("/conversations", json={"title": "Created conversation"})

    assert response.status_code == 201
    assert response.json() == {
        "id": str(conversation.id),
        "title": "Created conversation",
        "created_at": "2026-09-24T12:00:00Z",
        "updated_at": "2026-09-24T13:00:00Z",
    }
    service.create.assert_awaited_with(user.id, "Created conversation")


def test_list_conversations_returns_200_in_service_order(
    client_user_service: tuple[TestClient, User, Mock],
) -> None:
    client, user, service = client_user_service
    first = make_conversation(user.id, "First conversation")
    second = make_conversation(user.id, "Second conversation")
    service.list_for_user.return_value = [first, second]

    response = client.get("/conversations")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(first.id),
            "title": "First conversation",
            "created_at": "2026-09-24T12:00:00Z",
            "updated_at": "2026-09-24T13:00:00Z",
        },
        {
            "id": str(second.id),
            "title": "Second conversation",
            "created_at": "2026-09-24T12:00:00Z",
            "updated_at": "2026-09-24T13:00:00Z",
        },
    ]
    service.list_for_user.assert_awaited_with(user.id)


def test_get_conversation_returns_200(
    client_user_service: tuple[TestClient, User, Mock],
) -> None:
    client, user, service = client_user_service
    conversation = make_conversation(user.id, "Existing conversation")
    service.get.return_value = conversation

    response = client.get(f"/conversations/{conversation.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(conversation.id),
        "title": "Existing conversation",
        "created_at": "2026-09-24T12:00:00Z",
        "updated_at": "2026-09-24T13:00:00Z",
    }
    service.get.assert_awaited_with(conversation.id, user.id)


def test_get_missing_conversation_returns_404(
    client_user_service: tuple[TestClient, User, Mock],
) -> None:
    client, user, service = client_user_service
    conversation_id = uuid4()
    service.get.side_effect = ConversationNotFoundError("Private lookup details")

    response = client.get(f"/conversations/{conversation_id}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found"}
    service.get.assert_awaited_with(conversation_id, user.id)
