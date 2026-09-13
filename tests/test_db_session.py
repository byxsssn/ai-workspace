import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncpg
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.core import config


@pytest.mark.parametrize("raise_error", [False, True])
def test_db_session_lifecycle(
    monkeypatch: pytest.MonkeyPatch, raise_error: bool
) -> None:
    connect = Mock(side_effect=AssertionError("Database connections are forbidden"))
    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql+asyncpg://test:test@localhost:5433/test_workspace",
        ),
    )

    from ai_workspace.db.session import get_db_session

    provide_session = asynccontextmanager(get_db_session)
    sessions: list[AsyncSession] = []
    close_calls: list[AsyncMock] = []
    after_commit = Mock()

    async def exercise_dependency() -> None:
        async with provide_session() as first, provide_session() as second:
            assert first is not second
            for session in (first, second):
                assert isinstance(session, AsyncSession)
                assert session.sync_session.expire_on_commit is False
                sessions.append(session)
                close = AsyncMock(wraps=session.close)
                monkeypatch.setattr(session, "close", close)
                close_calls.append(close)
                event.listen(session.sync_session, "after_commit", after_commit)
                await session.begin()
                assert session.in_transaction()

            if raise_error:
                raise RuntimeError("Request failed")

    if raise_error:
        with pytest.raises(RuntimeError, match="Request failed"):
            asyncio.run(exercise_dependency())
    else:
        asyncio.run(exercise_dependency())

    for close in close_calls:
        close.assert_awaited_once()
    assert all(not session.in_transaction() for session in sessions)
    after_commit.assert_not_called()
    connect.assert_not_called()
