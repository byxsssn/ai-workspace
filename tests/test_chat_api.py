from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.api.dependencies.chat import get_agent_runner
from ai_workspace.api.routes import chat
from ai_workspace.db.session import get_db_session
from ai_workspace.main import app
from ai_workspace.models import User
from ai_workspace.providers import (
    ModelResponse,
    ProviderError,
    ReasoningEffort,
    TokenUsage,
)
from ai_workspace.services import (
    ChatService,
    ConversationNotFoundError,
    ProviderCredentialNotFoundError,
)


@pytest.fixture
def client_user_service(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, User, Mock]]:
    user = User(id=uuid4())
    session = AsyncMock(spec=AsyncSession)
    agent = Mock()
    service_factory = Mock(return_value=Mock(spec=ChatService))
    monkeypatch.setattr(chat, "ChatService", service_factory)

    async def override_current_user() -> User:
        return user

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield session

    with monkeypatch.context() as overrides:
        overrides.setitem(
            app.dependency_overrides, get_current_user, override_current_user
        )
        overrides.setitem(app.dependency_overrides, get_db_session, override_db_session)
        overrides.setitem(app.dependency_overrides, get_agent_runner, lambda: agent)
        with TestClient(app) as client:
            yield client, user, service_factory


@pytest.mark.parametrize(
    "usage",
    [TokenUsage(prompt_tokens=12, completion_tokens=3, total_tokens=15), None],
)
def test_chat_uses_authenticated_user_and_returns_provider_result(
    client_user_service: tuple[TestClient, User, Mock],
    monkeypatch: pytest.MonkeyPatch,
    usage: TokenUsage | None,
) -> None:
    client, user, service_factory = client_user_service
    service = service_factory.return_value
    service.complete_turn.return_value = ModelResponse(
        content="  回答\n保持原文  ", model="actual-model", usage=usage
    )
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
    service.complete_turn.assert_awaited_once_with(
        user_id=user.id,
        conversation_id=conversation_id,
        model="requested-model",
        content=content,
        reasoning_effort=None,
    )
    service_factory.assert_called_once()
    assert isinstance(service_factory.call_args.args[0], AsyncSession)
    assert (
        service_factory.call_args.args[1]
        is app.dependency_overrides[get_agent_runner]()
    )


@pytest.mark.parametrize(
    "reasoning_effort",
    [None, "none", "minimal", "low", "medium", "high", "xhigh", "max"],
)
def test_chat_passes_selected_reasoning_effort(
    client_user_service: tuple[TestClient, User, Mock],
    reasoning_effort: ReasoningEffort | None,
) -> None:
    client, user, service_factory = client_user_service
    service = service_factory.return_value
    service.complete_turn.return_value = ModelResponse(content="Answer", model="model")
    conversation_id = uuid4()

    response = client.post(
        f"/conversations/{conversation_id}/chat",
        json={
            "model": "model",
            "content": "Question",
            "reasoning_effort": reasoning_effort,
        },
    )

    assert response.status_code == 200
    service.complete_turn.assert_awaited_once_with(
        user_id=user.id,
        conversation_id=conversation_id,
        model="model",
        content="Question",
        reasoning_effort=reasoning_effort,
    )


@pytest.mark.parametrize("reasoning_effort", ["", "automatic", "HIGH", 1, True, [], {}])
def test_chat_rejects_invalid_reasoning_effort_before_service(
    client_user_service: tuple[TestClient, User, Mock], reasoning_effort: object
) -> None:
    client, _, service_factory = client_user_service

    response = client.post(
        f"/conversations/{uuid4()}/chat",
        json={
            "model": "model",
            "content": "Question",
            "reasoning_effort": reasoning_effort,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "reasoning_effort"]
    service_factory.assert_not_called()


def test_chat_openapi_exposes_optional_reasoning_effort_choices(
    client_user_service: tuple[TestClient, User, Mock],
) -> None:
    client, _, _ = client_user_service
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    schema = schemas["ChatRequest"]
    field = schema["properties"]["reasoning_effort"]

    assert "reasoning_effort" not in schema["required"]
    assert field["anyOf"] == [
        {"$ref": "#/components/schemas/ReasoningEffort"},
        {"type": "null"},
    ]
    assert schemas["ReasoningEffort"]["type"] == "string"
    assert schemas["ReasoningEffort"]["enum"] == [
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


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
            "Provider credential not configured",
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
def test_chat_maps_errors_without_exposing_sensitive_details(
    client_user_service: tuple[TestClient, User, Mock],
    error: Exception,
    status_code: int,
    detail: str,
) -> None:
    client, _, service_factory = client_user_service
    service_factory.return_value.complete_turn.side_effect = error

    response = client.post(
        f"/conversations/{uuid4()}/chat",
        json={"model": "requested-model", "content": "Question"},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


def test_chat_requires_authentication_and_rejects_invalid_or_user_selected_identity(
    client_user_service: tuple[TestClient, User, Mock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, service_factory = client_user_service
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


def test_shared_endpoint_preserves_learning_response_fields(client_user_service):
    from ai_workspace.learning.agent_models import LearningChatResponse

    client, _, service_factory = client_user_service
    result = LearningChatResponse(
        content="演示模式：学习回答。",
        model="model",
        source="demo",
        status="needs_clarification",
        sources=[],
        tool_calls=[],
    )
    service_factory.return_value.complete_turn.return_value = result
    response = client.post(
        f"/conversations/{uuid4()}/chat",
        json={
            "model": "model",
            "content": "继续学习",
        },
    )
    assert response.status_code == 200
    assert response.json() == result.model_dump()
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "body",
    [
        {"source": "demo"},
        {"learning_source": "notion"},
        {"page_id": "private"},
    ],
)
def test_shared_endpoint_does_not_take_learning_context_from_request(
    client_user_service, body
):
    client, _, service_factory = client_user_service
    response = client.post(
        f"/conversations/{uuid4()}/chat",
        json={
            "model": "model",
            "content": "question",
            **body,
        },
    )
    assert response.status_code == 422
    service_factory.assert_not_called()


def test_shared_endpoint_returns_safe_conflict_for_changed_history(client_user_service):
    from ai_workspace.services.turn import ConversationChangedError

    client, _, service_factory = client_user_service
    service_factory.return_value.complete_turn.side_effect = ConversationChangedError(
        "private"
    )
    response = client.post(
        f"/conversations/{uuid4()}/chat",
        json={
            "model": "model",
            "content": "question",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "conversation_changed"
    assert "private" not in response.text


def test_shared_endpoint_reports_learning_validation_without_echoing_input(
    client_user_service,
):
    from ai_workspace.learning.chat import LearningChatInputError

    client, _, service_factory = client_user_service
    errors = [
        {"loc": ["body", "content"], "type": "string_too_long", "msg": "Invalid value"}
    ]
    service_factory.return_value.complete_turn.side_effect = LearningChatInputError(
        errors
    )
    response = client.post(
        f"/conversations/{uuid4()}/chat",
        json={
            "model": "model",
            "content": "private question",
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": errors}
    assert "private question" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
