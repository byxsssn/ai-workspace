from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.models import ProviderCredential


class ProviderCredentialRepository:
    """在调用方管理的事务中读写加密凭据。"""

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
    ) -> ProviderCredential:
        credential = ProviderCredential(
            user_id=user_id,
            provider=provider,
            encrypted_api_key=encrypted_api_key,
        )
        self.session.add(credential)
        await self.session.flush()
        return credential

    async def update(
        self,
        credential: ProviderCredential,
        encrypted_api_key: str,
    ) -> ProviderCredential:
        credential.encrypted_api_key = encrypted_api_key
        await self.session.flush()
        # 异步响应序列化之前，先读取数据库生成的 updated_at。
        await self.session.refresh(credential)
        return credential

    async def delete(self, credential: ProviderCredential) -> None:
        await self.session.delete(credential)
        await self.session.flush()
