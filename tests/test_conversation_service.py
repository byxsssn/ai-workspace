import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.models import Conversation
from ai_workspace.repositories import ConversationRepository
from ai_workspace.services import ConversationNotFoundError, ConversationService


def test_create_normalizes_title_and_commits() -> None:
    user_id = uuid4()
    for title, expected_title in (
        ("  Project chat  ", "Project chat"),
        (" \t\n ", "New conversation"),
    ):
        session = AsyncMock(spec=AsyncSession)
        service = ConversationService(session)
        service.repository = Mock(spec=ConversationRepository)
        conversation = Conversation(user_id=user_id, title=expected_title)
        service.repository.create.return_value = conversation

        result = asyncio.run(service.create(user_id, title))

        assert result is conversation
        service.repository.create.assert_awaited_with(
            user_id=user_id, title=expected_title
        )
        session.commit.assert_awaited()


def test_create_rolls_back_and_reraises_write_errors() -> None:
    user_id = uuid4()
    for failure_stage in ("create", "commit"):
        session = AsyncMock(spec=AsyncSession)
        service = ConversationService(session)
        service.repository = Mock(spec=ConversationRepository)
        service.repository.create.return_value = Conversation(user_id=user_id)
        error = RuntimeError(f"{failure_stage} failed")
        if failure_stage == "create":
            service.repository.create.side_effect = error
        else:
            session.commit.side_effect = error

        with pytest.raises(RuntimeError) as exc_info:
            asyncio.run(service.create(user_id))

        assert exc_info.value is error
        session.rollback.assert_awaited()


def test_get_returns_owned_conversation_or_raises_not_found() -> None:
    user_id = uuid4()
    conversation_id = uuid4()
    service = ConversationService(AsyncMock(spec=AsyncSession))
    service.repository = Mock(spec=ConversationRepository)
    conversation = Conversation(id=conversation_id, user_id=user_id)
    service.repository.get_by_id_for_user.return_value = conversation

    assert asyncio.run(service.get(conversation_id, user_id)) is conversation

    service.repository.get_by_id_for_user.return_value = None
    with pytest.raises(ConversationNotFoundError, match="^Conversation not found$"):
        asyncio.run(service.get(conversation_id, user_id))

    service.repository.get_by_id_for_user.assert_awaited_with(conversation_id, user_id)


def test_list_for_user_returns_repository_list() -> None:
    user_id = uuid4()
    service = ConversationService(AsyncMock(spec=AsyncSession))
    service.repository = Mock(spec=ConversationRepository)
    conversations = [Conversation(user_id=user_id, title="Example")]
    service.repository.list_by_user.return_value = conversations

    result = asyncio.run(service.list_for_user(user_id))

    assert result is conversations
    service.repository.list_by_user.assert_awaited_with(user_id)
