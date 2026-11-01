"""聊天集成测试；服务提交仍处于外层回滚事务中。"""

import json
import logging
import traceback
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_workspace.core import encryption
from ai_workspace.core.config import get_settings
from ai_workspace.models import Conversation, User
from ai_workspace.providers import (
    ModelMessage,
    ModelResponse,
    ProviderError,
    ProviderId,
    ReasoningEffort,
    TokenUsage,
)
from ai_workspace.repositories import MessageRepository
from ai_workspace.services import (
    ChatService,
    ConversationNotFoundError,
    ProviderCredentialNotFoundError,
    ProviderCredentialService,
)

pytestmark = pytest.mark.anyio
API_KEY = "sk-or-chat-secret-must-not-appear"
HISTORY = [("user", "Earlier question"), ("assistant", "Earlier answer")]
ANSWER = "  回答\n保持原文  "


class FakeAgent:
    def __init__(
        self, session: AsyncSession, *, error: ProviderError | None = None
    ) -> None:
        self.session = session
        self.error = error
        self.calls: list[
            tuple[str, str, list[ModelMessage], ReasoningEffort | None]
        ] = []
        self.response = ModelResponse(
            content=ANSWER,
            model="returned-model",
            usage=TokenUsage(prompt_tokens=12, completion_tokens=3, total_tokens=15),
        )

    async def run(
        self,
        *,
        user_id: UUID,
        api_key: str,
        model: str,
        messages: Sequence[ModelMessage],
        source: str | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> ModelResponse:
        assert not self.session.in_transaction()
        assert isinstance(user_id, UUID)
        assert source is None
        self.calls.append((api_key, model, list(messages), reasoning_effort))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def db_session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    settings = SimpleNamespace(
        credential_encryption_key=SecretStr(Fernet.generate_key().decode("ascii"))
    )
    monkeypatch.setattr(encryption, "get_settings", lambda: settings)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                async with AsyncSession(
                    bind=connection,
                    join_transaction_mode="create_savepoint",
                    expire_on_commit=False,
                ) as session:
                    yield session
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def chat_ids(db_session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    users = [
        User(
            email=f"chat-{uuid4().hex}@example.invalid",
            password_hash="already-hashed-test-password",
        )
        for _ in range(2)
    ]
    db_session.add_all(users)
    await db_session.flush()
    conversations = [Conversation(user_id=user.id) for user in users]
    db_session.add_all(conversations)
    await db_session.flush()
    ids = users[0].id, users[1].id, conversations[0].id
    repository = MessageRepository(db_session)
    for role, content in HISTORY:
        await repository.create(ids[2], role, content)
    await repository.create(conversations[1].id, "user", "Other user's message")
    await db_session.commit()
    return ids


async def persisted_messages(
    session: AsyncSession, conversation_id: UUID
) -> list[tuple[str, str]]:
    session.expunge_all()
    messages = await MessageRepository(session).list_by_conversation(conversation_id)
    return [(message.role, message.content) for message in messages]


async def conversation_updated_at(
    session: AsyncSession, conversation_id: UUID
) -> datetime:
    conversation = await session.get(Conversation, conversation_id)
    assert conversation is not None
    return conversation.updated_at


async def test_complete_chat_preserves_context_content_and_provider_result(
    db_session: AsyncSession,
    chat_ids: tuple[UUID, UUID, UUID],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    owner_id, _, conversation_id = chat_ids
    await ProviderCredentialService(db_session).set_api_key(
        owner_id, ProviderId.OPENROUTER, API_KEY
    )
    previous_updated_at = await conversation_updated_at(db_session, conversation_id)
    agent = FakeAgent(db_session)
    expected = HISTORY.copy()
    service = ChatService(db_session, agent)
    get_credential = AsyncMock(wraps=service.credential_service.get)
    monkeypatch.setattr(service.credential_service, "get", get_credential)
    turns: list[tuple[str, ReasoningEffort | None]] = [
        ("  本次问题\n保留空白  ", "high"),
        ("Follow-up question", None),
    ]
    for content, reasoning_effort in turns:
        result = await service.complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="requested-model",
            content=content,
            reasoning_effort=reasoning_effort,
        )
        expected.append(("user", content))
        api_key, model, messages, requested_effort = agent.calls[-1]
        assert api_key == API_KEY
        assert model == "requested-model"
        assert requested_effort == reasoning_effort
        assert [(message.role, message.content) for message in messages] == expected
        get_credential.assert_awaited_with(owner_id, ProviderId.OPENROUTER)
        assert result is agent.response
        assert result.content == ANSWER
        assert result.model == "returned-model"
        assert result.usage == TokenUsage(
            prompt_tokens=12, completion_tokens=3, total_tokens=15
        )
        assert API_KEY not in repr(result)
        expected.append(("assistant", ANSWER))
        assert await persisted_messages(db_session, conversation_id) == expected
        updated_at = await conversation_updated_at(db_session, conversation_id)
        assert updated_at > previous_updated_at
        previous_updated_at = updated_at
    assert len(agent.calls) == 2
    assert get_credential.await_count == 2
    assert API_KEY not in caplog.text


@pytest.mark.parametrize("rejection", ["foreign", "missing", "credential"])
async def test_rejects_unavailable_conversation_or_credential_before_provider(
    db_session: AsyncSession,
    chat_ids: tuple[UUID, UUID, UUID],
    rejection: str,
) -> None:
    owner_id, other_id, conversation_id = chat_ids
    user_id = owner_id
    target_id = conversation_id
    error = ConversationNotFoundError
    if rejection == "credential":
        await ProviderCredentialService(db_session).set_api_key(
            other_id, ProviderId.OPENROUTER, API_KEY
        )
        error = ProviderCredentialNotFoundError
    else:
        user_id = other_id
        if rejection == "missing":
            target_id = uuid4()

    agent = FakeAgent(db_session)
    service = ChatService(db_session, agent)
    with pytest.raises(error):
        await service.complete_turn(
            user_id=user_id,
            conversation_id=target_id,
            model="requested-model",
            content="Rejected message",
        )
    assert agent.calls == []
    assert await persisted_messages(db_session, conversation_id) == HISTORY


async def test_provider_failure_preserves_history_and_keeps_credentials_private(
    db_session: AsyncSession,
    chat_ids: tuple[UUID, UUID, UUID],
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner_id, _, conversation_id = chat_ids
    await ProviderCredentialService(db_session).set_api_key(
        owner_id, ProviderId.OPENROUTER, API_KEY
    )
    previous_updated_at = await conversation_updated_at(db_session, conversation_id)
    agent = FakeAgent(
        db_session, error=ProviderError("Provider request failed", status_code=401)
    )
    service = ChatService(db_session, agent)
    with pytest.raises(ProviderError) as exc_info:
        await service.complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="requested-model",
            content="Failed message",
        )
    assert exc_info.value is agent.error
    assert len(agent.calls) == 1
    assert await persisted_messages(db_session, conversation_id) == HISTORY
    assert (
        await conversation_updated_at(db_session, conversation_id)
        == previous_updated_at
    )
    assert API_KEY not in "".join(traceback.format_exception(exc_info.value))
    assert API_KEY not in caplog.text


@pytest.mark.parametrize("failure", ["assistant_write", "commit"])
async def test_persistence_failure_rolls_back_both_messages(
    db_session: AsyncSession,
    chat_ids: tuple[UUID, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    owner_id, _, conversation_id = chat_ids
    await ProviderCredentialService(db_session).set_api_key(
        owner_id, ProviderId.OPENROUTER, API_KEY
    )
    previous_updated_at = await conversation_updated_at(db_session, conversation_id)
    agent = FakeAgent(db_session)
    service = ChatService(db_session, agent)
    create = service.message_repository.create

    async def fail_assistant(conversation_id: UUID, role: str, content: str):
        if role == "assistant":
            raise RuntimeError("Persistence failed")
        return await create(conversation_id, role, content)

    if failure == "assistant_write":
        monkeypatch.setattr(service.message_repository, "create", fail_assistant)
    else:
        monkeypatch.setattr(
            db_session,
            "commit",
            AsyncMock(side_effect=RuntimeError("Persistence failed")),
        )
    with pytest.raises(RuntimeError, match="Persistence failed"):
        await service.complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="requested-model",
            content="Failed message",
        )
    assert len(agent.calls) == 1
    assert await persisted_messages(db_session, conversation_id) == HISTORY
    assert (
        await conversation_updated_at(db_session, conversation_id)
        == previous_updated_at
    )


async def test_unified_runner_sdk_uses_saved_key_and_persists_the_reply(
    db_session: AsyncSession,
    chat_ids: tuple[UUID, UUID, UUID],
) -> None:
    import httpx
    import httpx2

    from ai_workspace.agents.runner import AgentRunner
    from ai_workspace.learning.config import LearningSettings
    from ai_workspace.learning.service import LearningService
    from ai_workspace.providers.openrouter import open_chat_model

    owner_id, _, conversation_id = chat_ids
    await ProviderCredentialService(db_session).set_api_key(
        owner_id, ProviderId.OPENROUTER, API_KEY
    )
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert not db_session.in_transaction()
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "object": "chat.completion",
                "id": "gen-persistence-test",
                "created": 1,
                "model": "returned-model",
                "system_fingerprint": None,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": ANSWER,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                },
            },
        )

    transport = httpx.MockTransport(handle)

    def model_factory(**kwargs):
        return open_chat_model(transport=transport, **kwargs)

    content = "Question through the shared SDK"
    async with httpx2.AsyncClient() as source_client:
        agent = AgentRunner(
            LearningService(LearningSettings(_env_file=None), source_client),
            model_factory=model_factory,
        )
        result = await ChatService(db_session, agent).complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="requested-model",
            content=content,
            reasoning_effort="high",
        )

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    payload = json.loads(request.content)
    assert payload["model"] == "requested-model"
    assert payload["reasoning"]["effort"] == "high"
    assert [
        (message["role"], message["content"]) for message in payload["messages"][-3:]
    ] == [
        *HISTORY,
        ("user", content),
    ]
    assert result.content == ANSWER
    assert result.model == "returned-model"
    assert result.usage == TokenUsage(
        prompt_tokens=12, completion_tokens=3, total_tokens=15
    )
    assert await persisted_messages(db_session, conversation_id) == [
        *HISTORY,
        ("user", content),
        ("assistant", ANSWER),
    ]
