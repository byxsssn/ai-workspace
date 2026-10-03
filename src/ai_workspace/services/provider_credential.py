from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.core.encryption import encrypt_api_key
from ai_workspace.models import ProviderCredential
from ai_workspace.providers.types import ProviderId
from ai_workspace.repositories import ProviderCredentialRepository


class ProviderCredentialNotFoundError(Exception):
    """The provider credential is unavailable to the specified user."""


class ProviderCredentialService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = ProviderCredentialRepository(session)

    async def set_api_key(
        self, user_id: UUID, provider: ProviderId, api_key: str
    ) -> ProviderCredential:
        """Create or replace the user's encrypted API key for this provider."""
        try:
            credential = await self.repository.get_by_user_and_provider(
                user_id, provider.value
            )
            encrypted_api_key = encrypt_api_key(api_key)
            if credential is None:
                credential = await self.repository.create(
                    user_id=user_id,
                    provider=provider.value,
                    encrypted_api_key=encrypted_api_key,
                )
            else:
                credential = await self.repository.update(
                    credential,
                    encrypted_api_key=encrypted_api_key,
                )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return credential

    async def get(self, user_id: UUID, provider: ProviderId) -> ProviderCredential:
        credential = await self.repository.get_by_user_and_provider(
            user_id, provider.value
        )
        if credential is None:
            raise ProviderCredentialNotFoundError("Provider credential not found")
        return credential

    async def delete(self, user_id: UUID, provider: ProviderId) -> None:
        credential = await self.get(user_id, provider)
        try:
            await self.repository.delete(credential)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
