import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.models import Message
from ai_workspace.repositories import MessageRepository
from ai_workspace.services import InvalidMessageRoleError, MessageService


def test_create_preserves_content_and_commits() -> None:
    conversation_id = uuid4()
    content = "  First line\nSecond line  \n "
    for role in ("user", "assistant"):
        session = AsyncMock(spec=AsyncSession)
        service = MessageService(session)
        service.repository = Mock(spec=MessageRepository)
        message = Message(conversation_id=conversation_id, role=role, content=content)
        service.repository.create.return_value = message

        result = asyncio.run(service.create(conversation_id, role, content))

        assert result is message
        service.repository.create.assert_awaited_with(
            conversation_id=conversation_id, role=role, content=content
        )
        session.commit.assert_awaited()


def test_create_rolls_back_and_reraises_write_errors() -> None:
    conversation_id = uuid4()
    for failure_stage in ("create", "commit"):
        session = AsyncMock(spec=AsyncSession)
        service = MessageService(session)
        service.repository = Mock(spec=MessageRepository)
        service.repository.create.return_value = Message(
            conversation_id=conversation_id, role="user", content="Example"
        )
        error = RuntimeError(f"{failure_stage} failed")
        if failure_stage == "create":
            service.repository.create.side_effect = error
        else:
            session.commit.side_effect = error

        with pytest.raises(RuntimeError) as exc_info:
            asyncio.run(service.create(conversation_id, "user", "Example"))

        assert exc_info.value is error
        session.rollback.assert_awaited()


def test_create_rejects_invalid_role_without_writing() -> None:
    session = AsyncMock(spec=AsyncSession)
    service = MessageService(session)
    service.repository = Mock(spec=MessageRepository)

    with pytest.raises(InvalidMessageRoleError, match="^Invalid message role$"):
        asyncio.run(service.create(uuid4(), "system", "Example"))

    service.repository.create.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_list_by_conversation_returns_repository_list_without_writing() -> None:
    conversation_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    service = MessageService(session)
    service.repository = Mock(spec=MessageRepository)
    messages = [
        Message(conversation_id=conversation_id, role="user", content="Example")
    ]
    service.repository.list_by_conversation.return_value = messages

    result = asyncio.run(service.list_by_conversation(conversation_id))

    assert result is messages
    service.repository.list_by_conversation.assert_awaited_with(conversation_id)
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
