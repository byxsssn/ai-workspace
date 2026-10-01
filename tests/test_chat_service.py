"""Chat integration tests; service commits remain inside an outer rollback."""

import json
import logging
import traceback
from collections.abc import AsyncIterator
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx2
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_workspace.core import encryption
from ai_workspace.core.config import get_settings
from ai_workspace.models import Conversation, User
from ai_workspace.providers import OpenRouterProvider, ProviderError, TokenUsage
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


def completion() -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "model": "returned-model",
            "choices": [{"message": {"content": ANSWER}}],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 3,
                "total_tokens": 15,
            },
        },
    )


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
) -> None:
    caplog.set_level(logging.DEBUG)
    owner_id, _, conversation_id = chat_ids
    await ProviderCredentialService(db_session).save_openrouter(owner_id, API_KEY)
    previous_updated_at = await conversation_updated_at(db_session, conversation_id)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return completion()

    expected = HISTORY.copy()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        service = ChatService(db_session, OpenRouterProvider(client))
        for content in ["  本次问题\n保留空白  ", "Follow-up question"]:
            result = await service.chat_completion(
                user_id=owner_id,
                conversation_id=conversation_id,
                model="requested-model",
                content=content,
            )
            expected.append(("user", content))
            assert json.loads(requests[-1].content) == {
                "model": "requested-model",
                "messages": [
                    {"role": role, "content": text} for role, text in expected
                ],
                "stream": False,
            }
            assert requests[-1].headers["Authorization"] == f"Bearer {API_KEY}"
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
    assert len(requests) == 2
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
        await ProviderCredentialService(db_session).save_openrouter(other_id, API_KEY)
        error = ProviderCredentialNotFoundError
    else:
        user_id = other_id
        if rejection == "missing":
            target_id = uuid4()

    def handle(_: httpx2.Request) -> httpx2.Response:
        pytest.fail("Rejected chat must not reach the provider")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        service = ChatService(db_session, OpenRouterProvider(client))
        with pytest.raises(error):
            await service.chat_completion(
                user_id=user_id,
                conversation_id=target_id,
                model="requested-model",
                content="Rejected message",
            )
    assert await persisted_messages(db_session, conversation_id) == HISTORY


async def test_provider_failure_preserves_history_and_keeps_credentials_private(
    db_session: AsyncSession,
    chat_ids: tuple[UUID, UUID, UUID],
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner_id, _, conversation_id = chat_ids
    await ProviderCredentialService(db_session).save_openrouter(owner_id, API_KEY)
    previous_updated_at = await conversation_updated_at(db_session, conversation_id)
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(401, json={"error": {"message": API_KEY}})
    )
    async with httpx2.AsyncClient(transport=transport) as client:
        service = ChatService(db_session, OpenRouterProvider(client))
        with pytest.raises(ProviderError) as exc_info:
            await service.chat_completion(
                user_id=owner_id,
                conversation_id=conversation_id,
                model="requested-model",
                content="Failed message",
            )
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
    await ProviderCredentialService(db_session).save_openrouter(owner_id, API_KEY)
    previous_updated_at = await conversation_updated_at(db_session, conversation_id)
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: completion())
    ) as client:
        service = ChatService(db_session, OpenRouterProvider(client))
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
            await service.chat_completion(
                user_id=owner_id,
                conversation_id=conversation_id,
                model="requested-model",
                content="Failed message",
            )
    assert await persisted_messages(db_session, conversation_id) == HISTORY
    assert (
        await conversation_updated_at(db_session, conversation_id)
        == previous_updated_at
    )
