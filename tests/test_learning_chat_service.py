"""学习会话持久化：真实 PostgreSQL 事务、虚构 Agent，不调用外部模型。"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_workspace.core import encryption
from ai_workspace.core.config import get_settings
from ai_workspace.learning.agent_models import LearningChatResponse
from ai_workspace.learning.chat import (
    LearningChatService,
    LearningConversationChangedError,
)
from ai_workspace.models import Conversation, Message, User
from ai_workspace.providers import ModelMessage, ProviderId
from ai_workspace.repositories import MessageRepository
from ai_workspace.services import (
    ConversationNotFoundError,
    ProviderCredentialNotFoundError,
    ProviderCredentialService,
)

pytestmark = pytest.mark.anyio
API_KEY = "sk-or-learning-persistence-test-only"
HISTORY = [("user", "先前的问题"), ("assistant", "先前的回答")]
ANSWER = "演示模式：学习进度来自虚构示例。\n\n参考来源：https://fastapi.tiangolo.com/"


class FakeAgent:
    def __init__(
        self,
        *,
        session: AsyncSession,
        error: BaseException | None = None,
        during_run: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.session = session
        self.error = error
        self.during_run = during_run
        self.calls: list[dict[str, object]] = []
        self.response = LearningChatResponse(
            content=ANSWER,
            model="test-learning-model",
            source="demo",
            status="answered",
            sources=[],
            tool_calls=[],
        )

    async def run(
        self,
        *,
        user_id: UUID,
        api_key: str,
        model: str,
        messages: Sequence[ModelMessage],
        source: str,
    ) -> LearningChatResponse:
        assert not self.session.in_transaction()
        self.calls.append(
            {
                "user_id": user_id,
                "api_key": api_key,
                "model": model,
                "messages": list(messages),
                "source": source,
            }
        )
        if self.during_run is not None:
            await self.during_run()
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
            email=f"learning-chat-{uuid4().hex}@example.invalid",
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
    await repository.create(conversations[1].id, "user", "其他用户的私人消息")
    await db_session.commit()
    return ids


async def stored_messages(
    session: AsyncSession, conversation_id: UUID
) -> list[tuple[str, str]]:
    rows = await session.execute(
        select(Message.role, Message.content)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return [(row.role, row.content) for row in rows]


async def configure_credential(session: AsyncSession, owner_id: UUID) -> None:
    await ProviderCredentialService(session).set_api_key(
        owner_id, ProviderId.OPENROUTER, API_KEY
    )


async def test_releases_read_transaction_and_preserves_two_turns(
    db_session: AsyncSession, chat_ids: tuple[UUID, UUID, UUID]
) -> None:
    owner_id, _, conversation_id = chat_ids
    await configure_credential(db_session, owner_id)
    agent = FakeAgent(session=db_session)
    service = LearningChatService(db_session, agent)
    expected = HISTORY.copy()
    previous = await db_session.scalar(
        select(Conversation.updated_at).where(Conversation.id == conversation_id)
    )
    for content in ("继续学习", "再解释一下上个例子"):
        response = await service.complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="test-learning-model",
            content=content,
            source="demo",
        )
        assert response is agent.response
        call = agent.calls[-1]
        assert call["user_id"] == owner_id
        assert call["api_key"] == API_KEY
        assert call["source"] == "demo"
        expected.append(("user", content))
        assert [(item.role, item.content) for item in call["messages"]] == expected
        expected.append(("assistant", ANSWER))
        assert await stored_messages(db_session, conversation_id) == expected
        current = await db_session.scalar(
            select(Conversation.updated_at).where(Conversation.id == conversation_id)
        )
        assert current > previous
        previous = current


@pytest.mark.parametrize("rejection", ["foreign", "missing", "credential"])
async def test_rejects_before_agent_and_does_not_read_foreign_credentials(
    db_session: AsyncSession,
    chat_ids: tuple[UUID, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    rejection: str,
) -> None:
    owner_id, other_id, conversation_id = chat_ids
    agent = FakeAgent(session=db_session)
    service = LearningChatService(db_session, agent)
    get_credential = AsyncMock(wraps=service.credential_service.get)
    monkeypatch.setattr(service.credential_service, "get", get_credential)
    if rejection == "credential":
        user_id, target_id, error = (
            owner_id,
            conversation_id,
            ProviderCredentialNotFoundError,
        )
    else:
        user_id = other_id
        target_id = conversation_id if rejection == "foreign" else uuid4()
        error = ConversationNotFoundError
    with pytest.raises(error):
        await service.complete_turn(
            user_id=user_id,
            conversation_id=target_id,
            model="test-learning-model",
            content="不应发送的问题",
        )
    assert not db_session.in_transaction()
    assert agent.calls == []
    assert get_credential.await_count == (1 if rejection == "credential" else 0)
    assert await stored_messages(db_session, conversation_id) == HISTORY


@pytest.mark.parametrize("cancelled", [False, True])
async def test_generation_failure_or_cancellation_does_not_store_a_turn(
    db_session: AsyncSession,
    chat_ids: tuple[UUID, UUID, UUID],
    cancelled: bool,
) -> None:
    owner_id, _, conversation_id = chat_ids
    await configure_credential(db_session, owner_id)
    error = asyncio.CancelledError() if cancelled else RuntimeError("Agent failed")
    agent = FakeAgent(session=db_session, error=error)
    with pytest.raises(type(error)):
        await LearningChatService(db_session, agent).complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="test-learning-model",
            content="失败的问题",
        )
    assert not db_session.in_transaction()
    assert len(agent.calls) == 1
    assert await stored_messages(db_session, conversation_id) == HISTORY


@pytest.mark.parametrize("change", ["append", "edit", "delete"])
async def test_rejects_answer_when_history_changed_during_generation(
    db_session: AsyncSession,
    chat_ids: tuple[UUID, UUID, UUID],
    change: str,
) -> None:
    owner_id, _, conversation_id = chat_ids
    await configure_credential(db_session, owner_id)
    expected = HISTORY.copy()

    async def change_history() -> None:
        if change == "append":
            await MessageRepository(db_session).create(
                conversation_id, "user", "另一轮消息"
            )
            expected.append(("user", "另一轮消息"))
        elif change == "edit":
            await db_session.execute(
                update(Message)
                .where(
                    Message.conversation_id == conversation_id, Message.role == "user"
                )
                .values(content="已经变更的问题")
            )
            expected[0] = ("user", "已经变更的问题")
        else:
            await db_session.execute(
                delete(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.role == "assistant",
                )
            )
            expected.pop()
        await db_session.commit()

    agent = FakeAgent(session=db_session, during_run=change_history)
    with pytest.raises(LearningConversationChangedError):
        await LearningChatService(db_session, agent).complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="test-learning-model",
            content="基于旧历史的问题",
        )
    assert not db_session.in_transaction()
    assert await stored_messages(db_session, conversation_id) == expected


async def test_rechecks_ownership_after_generation(
    db_session: AsyncSession, chat_ids: tuple[UUID, UUID, UUID]
) -> None:
    owner_id, other_id, conversation_id = chat_ids
    await configure_credential(db_session, owner_id)

    async def change_owner() -> None:
        await db_session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(user_id=other_id)
        )
        await db_session.commit()

    agent = FakeAgent(session=db_session, during_run=change_owner)
    with pytest.raises(ConversationNotFoundError):
        await LearningChatService(db_session, agent).complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="test-learning-model",
            content="生成期间失去归属",
        )
    assert await stored_messages(db_session, conversation_id) == HISTORY


@pytest.mark.parametrize("failure", ["assistant_write", "commit"])
async def test_persistence_failure_rolls_back_both_messages(
    db_session: AsyncSession,
    chat_ids: tuple[UUID, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    owner_id, _, conversation_id = chat_ids
    await configure_credential(db_session, owner_id)
    agent = FakeAgent(session=db_session)
    service = LearningChatService(db_session, agent)
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
            model="test-learning-model",
            content="写入失败的问题",
        )
    assert not db_session.in_transaction()
    assert await stored_messages(db_session, conversation_id) == HISTORY


@pytest.mark.parametrize("mode", ["count", "characters"])
async def test_context_is_bounded_without_cutting_individual_messages(
    db_session: AsyncSession,
    chat_ids: tuple[UUID, UUID, UUID],
    mode: str,
) -> None:
    owner_id, _, conversation_id = chat_ids
    await configure_credential(db_session, owner_id)
    repository = MessageRepository(db_session)
    for number in range(14):
        for role in ("user", "assistant"):
            content = f"{number}:{role}:" + (
                "字" * 2_000 if mode == "characters" else "内容"
            )
            await repository.create(conversation_id, role, content)
    await db_session.commit()
    original = await stored_messages(db_session, conversation_id)
    agent = FakeAgent(session=db_session)
    await LearningChatService(db_session, agent).complete_turn(
        user_id=owner_id,
        conversation_id=conversation_id,
        model="test-learning-model",
        content="最新问题",
    )
    messages = agent.calls[0]["messages"]
    assert len(messages) <= 20
    assert sum(len(message.content) for message in messages) <= 24_000
    assert messages[0].role == "user"
    assert messages[-1].content == "最新问题"
    assert [(message.role, message.content) for message in messages[:-1]] == original[
        -(len(messages) - 1) :
    ]
    assert await stored_messages(db_session, conversation_id) == [
        *original,
        ("user", "最新问题"),
        ("assistant", ANSWER),
    ]


class FakePlainProvider:
    provider_id = ProviderId.OPENROUTER

    def __init__(self, session, *, error=None, during_run=None):
        from ai_workspace.providers import ModelResponse

        self.session = session
        self.error = error
        self.during_run = during_run
        self.calls = []
        self.response = ModelResponse(content="普通聊天回答", model="plain/model")

    async def generate(self, **kwargs):
        assert not self.session.in_transaction()
        self.calls.append(kwargs)
        if self.during_run is not None:
            await self.during_run()
        if self.error is not None:
            raise self.error
        return self.response


async def stored_context(session, conversation_id):
    row = (
        await session.execute(
            select(Conversation.learning_topic, Conversation.learning_source).where(
                Conversation.id == conversation_id
            )
        )
    ).one()
    return tuple(row)


@pytest.mark.parametrize("source", [None, "demo", "notion"])
async def test_context_survives_creation_reload_and_rename(
    db_session, chat_ids, source
):
    from ai_workspace.services.conversation import ConversationService

    owner_id, _, _ = chat_ids
    service = ConversationService(db_session)
    conversation = await service.create(
        owner_id,
        "  学习  ",
        learning_topic="fastapi" if source else None,
        learning_source=source,
    )
    conversation_id = conversation.id
    db_session.expunge_all()
    reloaded = await service.get(conversation_id, owner_id)
    assert reloaded.learning_source == source
    assert reloaded.learning_topic == ("fastapi" if source else None)
    renamed = await service.update_title(conversation_id, owner_id, "新标题")
    assert renamed.learning_source == source
    db_session.expunge_all()
    listed = await service.list_for_user(owner_id)
    assert (
        next(item for item in listed if item.id == conversation_id).learning_source
        == source
    )


@pytest.mark.parametrize(
    "topic,source",
    [("fastapi", None), (None, "demo"), ("other", "demo"), ("fastapi", "other")],
)
async def test_database_rejects_partial_or_unknown_context(
    db_session, chat_ids, topic, source
):
    from sqlalchemy.exc import IntegrityError

    owner_id, _, _ = chat_ids
    db_session.add(
        Conversation(user_id=owner_id, learning_topic=topic, learning_source=source)
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.parametrize("source", ["demo", "notion"])
async def test_canonical_http_endpoint_dispatches_by_persisted_context(
    db_session, chat_ids, source, monkeypatch
):
    import httpx

    from ai_workspace.api.dependencies.auth import get_current_user
    from ai_workspace.api.routes import chat, learning_chat
    from ai_workspace.db.session import get_db_session
    from ai_workspace.main import create_api_app

    owner_id, _, _ = chat_ids
    await configure_credential(db_session, owner_id)
    agent = FakeAgent(session=db_session)
    agent.response = agent.response.model_copy(update={"source": source})
    provider = FakePlainProvider(db_session)
    monkeypatch.setattr(chat, "OpenRouterProvider", lambda: provider)
    app = create_api_app()
    app.dependency_overrides[get_current_user] = lambda: User(id=owner_id)

    async def session_override():
        yield db_session

    app.dependency_overrides[get_db_session] = session_override
    app.dependency_overrides[learning_chat.get_learning_agent] = lambda: agent
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/conversations",
            json={
                "title": "FastAPI",
                "learning_topic": "fastapi",
                "learning_source": source,
            },
        )
        assert created.status_code == 201
        conversation_id = UUID(created.json()["id"])
        for question in ("学习测试", "接着解释"):
            response = await client.post(
                f"/conversations/{conversation_id}/chat",
                json={
                    "model": "test/model",
                    "content": question,
                },
            )
            assert response.status_code == 200
            assert response.json()["source"] == source
            assert response.json()["tool_calls"] == []
        listed = (await client.get("/conversations")).json()
        item = next(item for item in listed if item["id"] == str(conversation_id))
        assert item["learning_source"] == source
    assert provider.calls == []
    assert len(agent.calls) == 2
    assert agent.calls[-1]["source"] == source
    assert agent.calls[-1]["user_id"] == owner_id
    assert [item.content for item in agent.calls[-1]["messages"]] == [
        "学习测试",
        ANSWER,
        "接着解释",
    ]
    assert len(await stored_messages(db_session, conversation_id)) == 4


@pytest.mark.parametrize("source", ["demo", "notion"])
async def test_legacy_binding_is_immutable_and_canonical_turn_reuses_it(
    db_session, chat_ids, source
):
    from ai_workspace.services.chat import ChatService
    from ai_workspace.services.turn import ConversationContextConflictError

    owner_id, _, conversation_id = chat_ids
    await configure_credential(db_session, owner_id)
    agent = FakeAgent(session=db_session)
    legacy = LearningChatService(db_session, agent)
    await legacy.complete_turn(
        user_id=owner_id,
        conversation_id=conversation_id,
        model="model",
        content="首次绑定",
        source=source,
    )
    assert await stored_context(db_session, conversation_id) == ("fastapi", source)
    with pytest.raises(ConversationContextConflictError):
        await legacy.complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="model",
            content="不能切换来源",
            source="notion" if source == "demo" else "demo",
        )
    assert len(agent.calls) == 1
    provider = FakePlainProvider(db_session)
    await ChatService(db_session, provider, learning_agent=agent).complete_turn(
        user_id=owner_id,
        conversation_id=conversation_id,
        model="model",
        content="继续学习",
    )
    assert agent.calls[-1]["source"] == source
    assert provider.calls == []
    assert len(await stored_messages(db_session, conversation_id)) == len(HISTORY) + 4


@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_legacy_generation_leaves_context_unbound(
    db_session, chat_ids, cancelled
):
    owner_id, _, conversation_id = chat_ids
    await configure_credential(db_session, owner_id)
    error = asyncio.CancelledError() if cancelled else RuntimeError("generation failed")
    agent = FakeAgent(session=db_session, error=error)
    with pytest.raises(type(error)):
        await LearningChatService(db_session, agent).complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="model",
            content="失败绑定",
        )
    assert not db_session.in_transaction()
    assert await stored_context(db_session, conversation_id) == (None, None)
    assert await stored_messages(db_session, conversation_id) == HISTORY


@pytest.mark.parametrize("learning", [False, True])
async def test_canonical_cancellation_releases_transaction_and_preserves_context(
    db_session, chat_ids, learning
):
    from ai_workspace.services.chat import ChatService

    owner_id, _, conversation_id = chat_ids
    await configure_credential(db_session, owner_id)
    if learning:
        await db_session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(
                learning_topic="fastapi",
                learning_source="notion",
            )
        )
        await db_session.commit()
    agent = FakeAgent(session=db_session, error=asyncio.CancelledError())
    provider = FakePlainProvider(db_session, error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await ChatService(db_session, provider, learning_agent=agent).complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="model",
            content="取消的问题",
        )
    assert not db_session.in_transaction()
    assert await stored_messages(db_session, conversation_id) == HISTORY
    assert await stored_context(db_session, conversation_id) == (
        ("fastapi", "notion") if learning else (None, None)
    )


@pytest.mark.parametrize(
    "invalid",
    [
        {"content": " "},
        {"content": "x" * 8001},
        {"model": "bad model"},
        {"model": "x" * 201},
        {"reasoning_effort": "high"},
    ],
)
async def test_learning_limits_do_not_change_ordinary_chat_contract(
    db_session, chat_ids, invalid
):
    from ai_workspace.learning.chat import LearningChatInputError
    from ai_workspace.services.chat import ChatService

    owner_id, _, conversation_id = chat_ids
    await configure_credential(db_session, owner_id)
    agent = FakeAgent(session=db_session)
    provider = FakePlainProvider(db_session)
    service = ChatService(db_session, provider, learning_agent=agent)
    kwargs = {
        "user_id": owner_id,
        "conversation_id": conversation_id,
        "model": "model",
        "content": "question",
    }
    kwargs.update(invalid)
    await service.complete_turn(**kwargs)
    assert len(provider.calls) == 1
    original = await stored_messages(db_session, conversation_id)
    await db_session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(
            learning_topic="fastapi",
            learning_source="demo",
        )
    )
    await db_session.commit()
    with pytest.raises(LearningChatInputError):
        await service.complete_turn(**kwargs)
    assert not db_session.in_transaction()
    assert agent.calls == []
    assert await stored_messages(db_session, conversation_id) == original


@pytest.mark.parametrize(
    "first_path,winner_path",
    [
        ("ordinary", "legacy-demo"),
        ("legacy-demo", "ordinary"),
        ("legacy-demo", "legacy-notion"),
        ("ordinary", "ordinary"),
    ],
)
async def test_both_paths_reject_stale_turn_after_competing_request_commits(
    db_session, chat_ids, first_path, winner_path
):
    from ai_workspace.services.chat import ChatService
    from ai_workspace.services.turn import ConversationChangedError

    owner_id, _, conversation_id = chat_ids
    await configure_credential(db_session, owner_id)

    async def run_path(session, path, content, during_run=None):
        if path == "ordinary":
            service = ChatService(
                session, FakePlainProvider(session, during_run=during_run)
            )
            return await service.complete_turn(
                user_id=owner_id,
                conversation_id=conversation_id,
                model="model",
                content=content,
            )
        service = LearningChatService(
            session, FakeAgent(session=session, during_run=during_run)
        )
        return await service.complete_turn(
            user_id=owner_id,
            conversation_id=conversation_id,
            model="model",
            content=content,
            source=path.removeprefix("legacy-"),
        )

    async def competing_request():
        # A distinct request/session finishes while the first request waits for
        # generation. Both sessions share the fixture's rollback-only connection.
        async with AsyncSession(
            bind=db_session.bind,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as other_session:
            await run_path(other_session, winner_path, "获胜请求")

    with pytest.raises(ConversationChangedError):
        await run_path(db_session, first_path, "过期请求", competing_request)
    assert not db_session.in_transaction()
    rows = await stored_messages(db_session, conversation_id)
    assert rows == [
        *HISTORY,
        ("user", "获胜请求"),
        ("assistant", "普通聊天回答" if winner_path == "ordinary" else ANSWER),
    ]
    assert await stored_context(db_session, conversation_id) == (
        (None, None)
        if winner_path == "ordinary"
        else ("fastapi", winner_path.removeprefix("legacy-"))
    )
