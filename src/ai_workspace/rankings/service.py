"""网页和 Agent 共用的快照读取、目录匹配与确定性排序。"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_workspace.db.session import async_session_factory
from ai_workspace.models.pypi_snapshot import PypiRefreshState, PypiSnapshot
from ai_workspace.rankings.catalog import (
    CATALOG,
    CATALOG_VERSION,
    CATEGORIES,
    normalize_project,
)
from ai_workspace.rankings.errors import RankingUnavailable
from ai_workspace.rankings.schemas import PypiRankingItem, PypiRankingResponse
from ai_workspace.rankings.source import (
    SOURCE_URL,
    ParsedSnapshot,
    fetch_snapshot,
    previous_month,
)

CACHE_TTL = timedelta(hours=24)
FAILURE_BACKOFF = timedelta(minutes=10)
REFRESH_LEASE = timedelta(seconds=60)


def _validate_filters(
    query: str, category: str | None, projects: Sequence[str] | None, limit: int
) -> None:
    if not isinstance(query, str) or len(query) > 100:
        raise ValueError("Invalid ranking query")
    if category is not None and category not in CATEGORIES:
        raise ValueError("Unknown ranking category")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Invalid ranking limit")
    if projects is not None:
        if isinstance(projects, str) or len(projects) > 20:
            raise ValueError("Invalid package selection")
        for name in projects:
            normalize_project(name)


def filter_rankings(
    response: PypiRankingResponse,
    *,
    query: str = "",
    category: str | None = None,
    projects: Sequence[str] | None = None,
    limit: int = 50,
) -> PypiRankingResponse:
    """在同一快照上筛选；Agent 可将完整响应固定在本轮，避免混用两次更新。"""
    _validate_filters(query, category, projects, limit)
    selected = (
        {normalize_project(name) for name in projects} if projects is not None else None
    )
    warnings = list(response.warnings)
    if selected is not None:
        unknown = selected - {entry.project for entry in CATALOG}
        if unknown:
            warnings.append(
                "以下包不在当前候选目录中：" + "、".join(sorted(unknown)) + "。"
            )
    needle = query.strip().casefold()
    items = [
        item
        for item in response.items
        if (category is None or item.category == category)
        and (selected is None or item.project in selected)
        and (
            not needle
            or needle
            in f"{item.project} {item.name} {item.description} {item.category_label}".casefold()
        )
    ]
    return response.model_copy(
        update={
            "items": items[:limit],
            "filtered_count": len(items),
            "warnings": warnings,
        }
    )


class PypiRankingService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
        *,
        fetcher: Callable[..., Awaitable[ParsedSnapshot]] = fetch_snapshot,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.sessions = session_factory
        self.fetcher = fetcher
        self.clock = clock

    async def list_rankings(
        self,
        *,
        query: str = "",
        category: str | None = None,
        projects: Sequence[str] | None = None,
        limit: int = 50,
        force_refresh: bool = False,
    ) -> PypiRankingResponse:
        _validate_filters(query, category, projects, limit)
        try:
            now = self.clock()
            snapshot, state = await self._read()
            due = not state or not state.next_check_at or state.next_check_at <= now
            if due or force_refresh:
                token = await self._claim(now, force_refresh)
                if token:
                    try:
                        parsed = await self.fetcher(now=now)
                    except RankingUnavailable:
                        await self._finish(token, parsed=None)
                    else:
                        await self._finish(token, parsed=parsed)
                snapshot, state = await self._read()
            if snapshot is None:
                raise RankingUnavailable()
            response = _build_response(snapshot, state, now=self.clock())
        except RankingUnavailable:
            raise
        except Exception:  # noqa: BLE001 - 外部与数据库异常不进入公开响应。
            raise RankingUnavailable() from None
        return filter_rankings(
            response, query=query, category=category, projects=projects, limit=limit
        )

    async def _read(self) -> tuple[PypiSnapshot | None, PypiRefreshState | None]:
        async with self.sessions() as session:
            snapshot = await session.scalar(
                select(PypiSnapshot)
                .order_by(
                    PypiSnapshot.source_updated_at.desc(),
                    PypiSnapshot.fetched_at.desc(),
                    PypiSnapshot.id.desc(),
                )
                .limit(1)
            )
            state = await session.get(PypiRefreshState, 1)
            # 对象离开会话后仍可读取；这里只读，不把事务传到网络或模型调用。
            return snapshot, state

    async def _claim(self, now: datetime, force: bool) -> UUID | None:
        token = uuid4()
        async with self.sessions.begin() as session:
            await session.execute(
                insert(PypiRefreshState)
                .values(id=1)
                .on_conflict_do_nothing(index_elements=[PypiRefreshState.id])
            )
            conditions = [
                PypiRefreshState.id == 1,
                or_(
                    PypiRefreshState.lease_until.is_(None),
                    PypiRefreshState.lease_until <= now,
                ),
            ]
            if not force:
                conditions.append(
                    or_(
                        PypiRefreshState.next_check_at.is_(None),
                        PypiRefreshState.next_check_at <= now,
                    )
                )
            claimed = await session.scalar(
                update(PypiRefreshState)
                .where(*conditions)
                .values(lease_token=token, lease_until=now + REFRESH_LEASE)
                .returning(PypiRefreshState.id)
            )
            return token if claimed else None

    async def _finish(self, token: UUID, *, parsed: ParsedSnapshot | None) -> None:
        now = self.clock()
        async with self.sessions.begin() as session:
            state = await session.scalar(
                select(PypiRefreshState)
                .where(PypiRefreshState.id == 1)
                .with_for_update()
            )
            if state is None or state.lease_token != token:
                return  # 租约过期后，较早的请求不能覆盖后来请求的结果。
            latest = await session.scalar(
                select(PypiSnapshot)
                .order_by(PypiSnapshot.source_updated_at.desc())
                .limit(1)
            )
            error = "upstream_unavailable" if parsed is None else None
            if (
                parsed is not None
                and latest is not None
                and parsed.source_updated_at < latest.source_updated_at
            ):
                error = "upstream_older"
            if parsed is not None and error is None:
                await session.execute(
                    insert(PypiSnapshot)
                    .values(
                        id=parsed.id,
                        source_updated_at=parsed.source_updated_at,
                        fetched_at=now,
                        period=parsed.period,
                        source_url=SOURCE_URL,
                        catalog_version=CATALOG_VERSION,
                        payload=parsed.payload,
                    )
                    .on_conflict_do_nothing(index_elements=[PypiSnapshot.id])
                )
            state.checked_at = now
            state.next_check_at = now + (FAILURE_BACKOFF if error else CACHE_TTL)
            state.lease_token = None
            state.lease_until = None
            state.last_error = error


def _build_response(
    snapshot: PypiSnapshot, state: PypiRefreshState | None, *, now: datetime
) -> PypiRankingResponse:
    downloads = {
        normalize_project(row["project"]): row["download_count"]
        for row in snapshot.payload["rows"]
    }
    ordered = sorted(
        CATALOG,
        key=lambda entry: (
            entry.project not in downloads,
            -downloads.get(entry.project, 0),
            entry.project,
        ),
    )
    items = [
        PypiRankingItem(
            project=entry.project,
            name=entry.name,
            category=entry.category,
            category_label=CATEGORIES[entry.category],
            description=entry.description,
            download_count=downloads.get(entry.project),
            rank=index if entry.project in downloads else None,
            pypi_url=f"https://pypi.org/project/{entry.project}/",
        )
        for index, entry in enumerate(ordered, start=1)
    ]
    warnings = []
    if snapshot.period < previous_month(now):
        warnings.append("上游尚未提供最近完整月份的数据，当前展示较早月份的快照。")
    if state and state.last_error:
        warnings.append("最近一次更新未成功，继续展示最近一次成功保存的快照。")
    if state and state.lease_until and state.lease_until > now:
        warnings.append("数据正在更新，当前展示已保存的快照。")
    matched = sum(item.download_count is not None for item in items)
    if matched < len(CATALOG):
        warnings.append("部分候选包未被本期上游快照收录；缺失值不代表零下载。")
    stale = snapshot.period < previous_month(now) or bool(state and state.last_error)
    return PypiRankingResponse(
        snapshot_id=snapshot.id,
        source_url=SOURCE_URL,
        period=snapshot.period,
        source_updated_at=snapshot.source_updated_at,
        fetched_at=snapshot.fetched_at,
        checked_at=state.checked_at
        if state and state.checked_at
        else snapshot.fetched_at,
        catalog_version=CATALOG_VERSION,
        candidate_count=len(CATALOG),
        matched_count=matched,
        source_count=len(downloads),
        filtered_count=len(items),
        stale=stale,
        warnings=warnings,
        categories=[{"id": key, "label": value} for key, value in CATEGORIES.items()],
        items=items,
    )
