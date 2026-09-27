from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.models import ProviderCredential


class ProviderCredentialRepository:
    """Access encrypted credentials within a caller-managed transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_and_provider(
        self, user_id: UUID, provider: str
    ) -> ProviderCredential | None:
        result = await self.session.execute(
            select(ProviderCredential).where(
                ProviderCredential.user_id == user_id,
                ProviderCredential.provider == provider,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id: UUID,
        provider: str,
        encrypted_api_key: str,
        base_url: str | None = None,
    ) -> ProviderCredential:
        credential = ProviderCredential(
            user_id=user_id,
            provider=provider,
            encrypted_api_key=encrypted_api_key,
            base_url=base_url,
        )
        self.session.add(credential)
        await self.session.flush()
        return credential

    async def update(
        self,
        credential: ProviderCredential,
        encrypted_api_key: str,
        base_url: str | None = None,
    ) -> ProviderCredential:
        credential.encrypted_api_key = encrypted_api_key
        credential.base_url = base_url
        await self.session.flush()
        # Load the database-generated updated_at before async response serialization.
        await self.session.refresh(credential)
        return credential

    async def delete(self, credential: ProviderCredential) -> None:
        await self.session.delete(credential)
        await self.session.flush()
