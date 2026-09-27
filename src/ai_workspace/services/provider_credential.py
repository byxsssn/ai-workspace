from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.core.encryption import encrypt_api_key
from ai_workspace.models import ProviderCredential
from ai_workspace.repositories import ProviderCredentialRepository

OPENROUTER_PROVIDER = "openrouter"


class ProviderCredentialNotFoundError(Exception):
    """The provider credential is unavailable to the specified user."""


class ProviderCredentialService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = ProviderCredentialRepository(session)

    async def save_openrouter(
        self, user_id: UUID, api_key: str, base_url: str | None = None
    ) -> ProviderCredential:
        try:
            credential = await self.repository.get_by_user_and_provider(
                user_id, OPENROUTER_PROVIDER
            )
            encrypted_api_key = encrypt_api_key(api_key)
            if credential is None:
                credential = await self.repository.create(
                    user_id=user_id,
                    provider=OPENROUTER_PROVIDER,
                    encrypted_api_key=encrypted_api_key,
                    base_url=base_url,
                )
            else:
                credential = await self.repository.update(
                    credential,
                    encrypted_api_key=encrypted_api_key,
                    base_url=base_url,
                )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return credential

    async def get_openrouter(self, user_id: UUID) -> ProviderCredential:
        credential = await self.repository.get_by_user_and_provider(
            user_id, OPENROUTER_PROVIDER
        )
        if credential is None:
            raise ProviderCredentialNotFoundError("Provider credential not found")
        return credential

    async def delete_openrouter(self, user_id: UUID) -> None:
        credential = await self.get_openrouter(user_id)
        try:
            await self.repository.delete(credential)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
