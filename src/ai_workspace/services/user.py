import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.core.security import hash_password
from ai_workspace.models import User
from ai_workspace.repositories import UserRepository


class EmailAlreadyRegisteredError(Exception):
    """The normalized email already belongs to a user."""


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = UserRepository(session)

    async def register(self, email: str, password: str) -> User:
        normalized_email = email.strip().lower()
        existing_user = await self.repository.get_by_email(normalized_email)
        if existing_user is not None:
            raise EmailAlreadyRegisteredError("Email is already registered")

        password_hash = await asyncio.to_thread(hash_password, password)
        try:
            user = await self.repository.create(
                email=normalized_email, password_hash=password_hash
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return user
