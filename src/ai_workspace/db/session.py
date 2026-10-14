from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai_workspace.core.config import get_settings

engine: AsyncEngine = create_async_engine(
    get_settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=5
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """提供数据库会话，由调用方管理事务，不在此处提交。"""
    async with async_session_factory() as session:
        yield session
