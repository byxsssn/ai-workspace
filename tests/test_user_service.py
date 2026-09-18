import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.core.security import verify_password
from ai_workspace.models import User
from ai_workspace.repositories import UserRepository
from ai_workspace.services import EmailAlreadyRegisteredError, UserService
from ai_workspace.services import user as user_service_module


def test_service_and_repository_share_the_session() -> None:
    session = AsyncMock(spec=AsyncSession)

    service = UserService(session)

    assert service.session is session
    assert isinstance(service.repository, UserRepository)
    assert service.repository.session is session


def test_register_normalizes_email_hashes_password_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = Mock(spec=UserRepository)
    repository.get_by_email.return_value = None
    expected_user = User(email="example@example.invalid")
    repository.create.return_value = expected_user
    repository_factory = Mock(return_value=repository)
    monkeypatch.setattr(user_service_module, "UserRepository", repository_factory)
    password = "  preserve password spaces  "

    user = asyncio.run(
        UserService(session).register("  Example@Example.invalid  ", password)
    )

    assert user is expected_user
    repository_factory.assert_called_once_with(session)
    repository.get_by_email.assert_awaited_once_with("example@example.invalid")
    repository.create.assert_awaited_once()
    password_hash = repository.create.await_args.kwargs["password_hash"]
    repository.create.assert_awaited_once_with(
        email="example@example.invalid", password_hash=password_hash
    )
    assert password_hash != password
    assert verify_password(password, password_hash) is True
    assert verify_password(password.strip(), password_hash) is False
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_called()


def test_register_rejects_duplicate_before_hashing_or_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = Mock(spec=UserRepository)
    repository.get_by_email.return_value = User(email="existing@example.invalid")
    monkeypatch.setattr(
        user_service_module, "UserRepository", Mock(return_value=repository)
    )
    hash_password = Mock()
    monkeypatch.setattr(user_service_module, "hash_password", hash_password)

    with pytest.raises(EmailAlreadyRegisteredError):
        asyncio.run(
            UserService(session).register("  Existing@Example.invalid  ", "password")
        )

    repository.get_by_email.assert_awaited_once_with("existing@example.invalid")
    hash_password.assert_not_called()
    repository.create.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("failure_stage", ["create", "commit"])
def test_register_rolls_back_and_reraises_write_failures(
    monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = Mock(spec=UserRepository)
    repository.get_by_email.return_value = None
    repository.create.return_value = User(email="example@example.invalid")
    monkeypatch.setattr(
        user_service_module, "UserRepository", Mock(return_value=repository)
    )
    hash_password = Mock(return_value="test-password-hash")
    monkeypatch.setattr(user_service_module, "hash_password", hash_password)
    error = RuntimeError(f"{failure_stage} failed")
    if failure_stage == "create":
        repository.create.side_effect = error
    else:
        session.commit.side_effect = error

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(
            UserService(session).register("Example@Example.invalid", "password")
        )

    assert exc_info.value is error
    repository.get_by_email.assert_awaited_once_with("example@example.invalid")
    hash_password.assert_called_once_with("password")
    repository.create.assert_awaited_once_with(
        email="example@example.invalid", password_hash="test-password-hash"
    )
    session.rollback.assert_awaited_once_with()
    if failure_stage == "create":
        session.commit.assert_not_called()
    else:
        session.commit.assert_awaited_once_with()
