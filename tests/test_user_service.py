import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.core.security import verify_password
from ai_workspace.models import User
from ai_workspace.repositories import UserRepository
from ai_workspace.services import (
    EmailAlreadyRegisteredError,
    InactiveUserError,
    InvalidCredentialsError,
    UserService,
)
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


def test_authenticate_normalizes_email_verifies_password_and_returns_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = Mock(spec=UserRepository)
    expected_user = User(
        email="example@example.invalid",
        password_hash="stored-password-hash",
        is_active=True,
    )
    original_fields = {
        column.name: getattr(expected_user, column.name)
        for column in User.__table__.columns
    }
    repository.get_by_email.return_value = expected_user
    repository_factory = Mock(return_value=repository)
    monkeypatch.setattr(user_service_module, "UserRepository", repository_factory)
    verify_password_mock = Mock(return_value=True)
    monkeypatch.setattr(user_service_module, "verify_password", verify_password_mock)
    to_thread = AsyncMock(wraps=asyncio.to_thread)
    monkeypatch.setattr(user_service_module.asyncio, "to_thread", to_thread)
    password = "  preserve password spaces  "

    user = asyncio.run(
        UserService(session).authenticate("  Example@Example.invalid  ", password)
    )

    assert user is expected_user
    repository_factory.assert_called_once_with(session)
    repository.get_by_email.assert_awaited_once_with("example@example.invalid")
    to_thread.assert_awaited_once_with(
        verify_password_mock, password, "stored-password-hash"
    )
    verify_password_mock.assert_called_once_with(password, "stored-password-hash")
    assert {
        column.name: getattr(user, column.name) for column in User.__table__.columns
    } == original_fields
    repository.create.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    ("is_active", "password_valid", "expected_error", "expected_message"),
    [
        pytest.param(
            None,
            False,
            InvalidCredentialsError,
            "Invalid email or password",
            id="missing-user",
        ),
        pytest.param(
            True,
            False,
            InvalidCredentialsError,
            "Invalid email or password",
            id="wrong-password",
        ),
        pytest.param(
            False,
            True,
            InactiveUserError,
            "User is inactive",
            id="inactive-user",
        ),
        pytest.param(
            False,
            False,
            InvalidCredentialsError,
            "Invalid email or password",
            id="inactive-user-with-wrong-password",
        ),
    ],
)
def test_authenticate_rejects_invalid_or_inactive_users_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    is_active: bool | None,
    password_valid: bool,
    expected_error: type[Exception],
    expected_message: str,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = Mock(spec=UserRepository)
    stored_user = None
    original_fields = None
    if is_active is not None:
        stored_user = User(
            email="example@example.invalid",
            password_hash="stored-password-hash",
            is_active=is_active,
        )
        original_fields = {
            column.name: getattr(stored_user, column.name)
            for column in User.__table__.columns
        }
    repository.get_by_email.return_value = stored_user
    monkeypatch.setattr(
        user_service_module, "UserRepository", Mock(return_value=repository)
    )
    verify_password_mock = Mock(return_value=password_valid)
    monkeypatch.setattr(user_service_module, "verify_password", verify_password_mock)
    to_thread = AsyncMock(wraps=asyncio.to_thread)
    monkeypatch.setattr(user_service_module.asyncio, "to_thread", to_thread)
    password = "  preserve password spaces  "

    with pytest.raises(expected_error) as exc_info:
        asyncio.run(
            UserService(session).authenticate("  Example@Example.invalid  ", password)
        )

    assert str(exc_info.value) == expected_message
    repository.get_by_email.assert_awaited_once_with("example@example.invalid")
    if stored_user is None:
        verify_password_mock.assert_not_called()
        to_thread.assert_not_called()
    else:
        to_thread.assert_awaited_once_with(
            verify_password_mock, password, "stored-password-hash"
        )
        verify_password_mock.assert_called_once_with(password, "stored-password-hash")
        assert {
            column.name: getattr(stored_user, column.name)
            for column in User.__table__.columns
        } == original_fields
    repository.create.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
