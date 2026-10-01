from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.api.routes import chat
from ai_workspace.db.session import get_db_session
from ai_workspace.main import app
from ai_workspace.models import User
from ai_workspace.providers import ChatCompletionResponse, ProviderError, TokenUsage
from ai_workspace.services import (
    ChatService,
    ConversationNotFoundError,
    ProviderCredentialNotFoundError,
)


@pytest.fixture
def client_user_service(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, User, Mock, httpx2.AsyncClient]]:
    user = User(id=uuid4())
    session = AsyncMock(spec=AsyncSession)
    service_factory = Mock(return_value=Mock(spec=ChatService))
    monkeypatch.setattr(chat, "ChatService", service_factory)

    def unexpected_request(_: httpx2.Request) -> httpx2.Response:
        pytest.fail("API unit tests must not make upstream requests")

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(unexpected_request))
    monkeypatch.setattr(
        chat, "httpx2", SimpleNamespace(AsyncClient=Mock(return_value=http_client))
    )

    async def override_current_user() -> User:
        return user

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield session

    with monkeypatch.context() as overrides:
        overrides.setitem(
            app.dependency_overrides, get_current_user, override_current_user
        )
        overrides.setitem(app.dependency_overrides, get_db_session, override_db_session)
        with TestClient(app) as client:
            yield client, user, service_factory, http_client


@pytest.mark.parametrize(
    "usage",
    [TokenUsage(prompt_tokens=12, completion_tokens=3, total_tokens=15), None],
)
def test_chat_uses_authenticated_user_and_returns_provider_result(
    client_user_service: tuple[TestClient, User, Mock, httpx2.AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
    usage: TokenUsage | None,
) -> None:
    client, user, service_factory, http_client = client_user_service
    service = service_factory.return_value
    service.chat_completion.return_value = ChatCompletionResponse(
        content="  回答\n保持原文  ", model="actual-model", usage=usage
    )
    provider = Mock(spec=chat.OpenRouterProvider)
    provider_factory = Mock(return_value=provider)
    monkeypatch.setattr(chat, "OpenRouterProvider", provider_factory)
    conversation_id = uuid4()
    content = "  问题\n保持原文  "

    response = client.post(
        f"/conversations/{conversation_id}/chat",
        params={"user_id": str(uuid4())},
        json={"model": "requested-model", "content": content},
    )

    assert response.status_code == 200
    assert response.json() == {
        "content": "  回答\n保持原文  ",
        "model": "actual-model",
        "usage": usage.model_dump() if usage is not None else None,
    }
    service.chat_completion.assert_awaited_once_with(
        user_id=user.id,
        conversation_id=conversation_id,
        model="requested-model",
        content=content,
    )
    provider_factory.assert_called_once_with(http_client)
    service_factory.assert_called_once()
    assert isinstance(service_factory.call_args.args[0], AsyncSession)
    assert service_factory.call_args.args[1] is provider
    assert http_client.is_closed


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (
            ConversationNotFoundError("Private lookup details"),
            404,
            "Conversation not found",
        ),
        (
            ProviderCredentialNotFoundError("Private credential details"),
            400,
            "OpenRouter credential not configured",
        ),
        (
            ProviderError("Secret upstream body: sk-or-private", status_code=401),
            502,
            "Chat provider request failed",
        ),
        (
            ProviderError("Secret upstream body: sk-or-private", status_code=429),
            502,
            "Chat provider request failed",
        ),
        (
            ProviderError("Secret transport details: sk-or-private"),
            502,
            "Chat provider request failed",
        ),
    ],
)
def test_chat_maps_errors_and_closes_http_client(
    client_user_service: tuple[TestClient, User, Mock, httpx2.AsyncClient],
    error: Exception,
    status_code: int,
    detail: str,
) -> None:
    client, _, service_factory, http_client = client_user_service
    service_factory.return_value.chat_completion.side_effect = error

    response = client.post(
        f"/conversations/{uuid4()}/chat",
        json={"model": "requested-model", "content": "Question"},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert http_client.is_closed


def test_chat_requires_authentication_and_rejects_invalid_or_user_selected_identity(
    client_user_service: tuple[TestClient, User, Mock, httpx2.AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, service_factory, _ = client_user_service
    url = f"/conversations/{uuid4()}/chat"
    valid_payload = {"model": "requested-model", "content": "Question"}
    for payload in (
        {**valid_payload, "user_id": str(uuid4())},
        {"content": "Question"},
        {"model": "requested-model"},
        {**valid_payload, "model": ""},
        {**valid_payload, "content": ""},
    ):
        assert client.post(url, json=payload).status_code == 422

    with monkeypatch.context() as auth_override:
        auth_override.delitem(app.dependency_overrides, get_current_user)
        response = client.post(url, json=valid_payload)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    service_factory.assert_not_called()
