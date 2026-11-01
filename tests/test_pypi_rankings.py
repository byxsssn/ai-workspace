"""公开榜单的统计口径、持久缓存和跨请求隔离。"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from ai_workspace.api.dependencies.rankings import get_pypi_ranking_service
from ai_workspace.api.routes.rankings import router
from ai_workspace.core.config import get_settings
from ai_workspace.models.pypi_snapshot import PypiRefreshState, PypiSnapshot
from ai_workspace.rankings import source
from ai_workspace.rankings.catalog import CATALOG, normalize_project
from ai_workspace.rankings.errors import RankingUnavailable
from ai_workspace.rankings.service import (
    PypiRankingService,
    _build_response,
    filter_rankings,
)
from ai_workspace.rankings.source import SOURCE_URL, fetch_snapshot, parse_snapshot

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


def payload(*, updated="2026-11-01 06:40:00", rows=None) -> dict:
    rows = (
        rows
        if rows is not None
        else [
            {"project": "FastAPI", "download_count": 100},
            {"project": "flask", "download_count": 100},
            {"project": "django", "download_count": 80},
            {"project": "some-unselected-package", "download_count": 1000},
        ]
    )
    return {
        "source": "ClickHouse",
        "last_update": updated,
        "total_rows": len(rows),
        "rows": rows,
    }


def parsed(**kwargs):
    return parse_snapshot(json.dumps(payload(**kwargs)).encode(), now=NOW)


def example_response():
    value = parsed()
    snapshot = PypiSnapshot(
        id=value.id,
        source_updated_at=value.source_updated_at,
        period=value.period,
        fetched_at=NOW,
        source_url=SOURCE_URL,
        catalog_version="test",
        payload=value.payload,
    )
    return _build_response(snapshot, None, now=NOW)


def test_parser_derives_previous_calendar_month_and_hash():
    result = parsed(updated="2026-01-01 01:00:00")
    assert result.period == "2025-12"
    assert result.source_updated_at.tzinfo == UTC
    assert len(result.id) == 64
    assert result.payload["rows"][0]["project"] == "FastAPI"


@pytest.mark.parametrize(
    "change",
    [
        {"source": "untrusted"},
        {"exception": "upstream query failed"},
        {"error": {"message": "upstream query failed"}},
        {"last_update": "2026-13-01 00:00:00"},
        {"last_update": "2027-01-01 00:00:00"},
        {"last_update": None},
        {"rows": []},
        {"rows": {}},
        {"total_rows": 15_000},
        {"total_rows": True},
    ],
)
def test_invalid_source_metadata_is_rejected(change):
    value = payload()
    value.update(change)
    with pytest.raises(RankingUnavailable):
        parse_snapshot(json.dumps(value).encode(), now=NOW)


@pytest.mark.parametrize(
    "rows",
    [
        [{"project": "foo", "download_count": True}],
        [{"project": "foo", "download_count": -1}],
        [{"project": "foo", "download_count": "12"}],
        [{"project": "foo", "download_count": 1.5}],
        [{"project": "foo", "download_count": 9_007_199_254_740_992}],
        [{"project": "https://example.com", "download_count": 12}],
        [{"project": "-foo", "download_count": 12}],
        [{"project": "foo"}],
        [
            {"project": "Foo_Bar", "download_count": 12},
            {"project": "foo.bar", "download_count": 10},
        ],
    ],
)
def test_invalid_download_rows_are_rejected(rows):
    with pytest.raises(RankingUnavailable):
        parsed(rows=rows)


def test_selected_catalog_rank_missing_and_filters_share_snapshot():
    response = example_response()
    assert len(CATALOG) == response.candidate_count == 20
    assert response.source_count == 4 and response.matched_count == 3
    assert [(x.project, x.download_count, x.rank) for x in response.items[:3]] == [
        ("fastapi", 100, 1),
        ("flask", 100, 2),
        ("django", 80, 3),
    ]
    assert all(x.download_count is None and x.rank is None for x in response.items[3:])
    selected = filter_rankings(response, projects=["FLASK", "not-in-catalog"])
    assert [x.project for x in selected.items] == ["flask"]
    assert selected.snapshot_id == response.snapshot_id
    assert selected.items[0].rank == 2
    assert "not-in-catalog" in selected.warnings[-1]
    assert filter_rankings(response, projects=[]).items == []
    assert filter_rankings(response, query="异步 HTTP").items[0].project == "httpx"
    assert filter_rankings(response, category="web", limit=2).filtered_count == 4
    assert len(filter_rankings(response, category="web", limit=2).items) == 2
    assert len(response.items) == 20


@pytest.mark.parametrize(
    "kwargs",
    [
        {"category": "unknown"},
        {"projects": ["https://bad"]},
        {"limit": 0},
        {"limit": True},
        {"query": "x" * 101},
    ],
)
def test_invalid_filters_are_controlled(kwargs):
    with pytest.raises(ValueError):
        filter_rankings(example_response(), **kwargs)


def test_zero_is_real_value_but_missing_is_unknown():
    value = parsed(rows=[{"project": "fastapi", "download_count": 0}])
    snapshot = PypiSnapshot(
        id=value.id,
        source_updated_at=value.source_updated_at,
        period=value.period,
        fetched_at=NOW,
        source_url=SOURCE_URL,
        catalog_version="test",
        payload=value.payload,
    )
    response = _build_response(snapshot, None, now=NOW)
    assert (
        response.items[0].project == "fastapi" and response.items[0].download_count == 0
    )
    assert response.items[0].rank == 1
    assert response.items[1].download_count is None


def test_catalog_names_normalized_unique():
    assert len({normalize_project(x.project) for x in CATALOG}) == len(CATALOG)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    schema = "test_pypi_" + uuid4().hex
    engine = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
        execution_options={"schema_translate_map": {None: schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            await connection.run_sync(lambda conn: PypiSnapshot.__table__.create(conn))
            await connection.run_sync(
                lambda conn: PypiRefreshState.__table__.create(conn)
            )
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await engine.dispose()


@pytest.mark.anyio
async def test_cache_shared_across_service_instances_and_survives_failure(sessions):
    clock = [NOW]
    fetcher = AsyncMock(return_value=parsed())
    first = PypiRankingService(sessions, fetcher=fetcher, clock=lambda: clock[0])
    result = await first.list_rankings()
    second = PypiRankingService(sessions, fetcher=fetcher, clock=lambda: clock[0])
    assert (await second.list_rankings()).snapshot_id == result.snapshot_id
    fetcher.assert_awaited_once()
    clock[0] += timedelta(hours=25)
    fetcher.side_effect = RankingUnavailable()
    failed = await second.list_rankings()
    assert failed.snapshot_id == result.snapshot_id and failed.stale
    assert failed.checked_at == clock[0]
    assert failed.fetched_at == NOW
    assert "更新未成功" in failed.warnings[0]
    await first.list_rankings()
    assert fetcher.await_count == 2
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PypiSnapshot)) == 1


@pytest.mark.anyio
async def test_empty_cache_failure_is_shared_and_backed_off(sessions):
    fetcher = AsyncMock(side_effect=RankingUnavailable())
    for _ in range(2):
        with pytest.raises(RankingUnavailable):
            await PypiRankingService(
                sessions, fetcher=fetcher, clock=lambda: NOW
            ).list_rankings()
    fetcher.assert_awaited_once()


@pytest.mark.anyio
async def test_successful_fetch_of_old_month_stays_stale(sessions):
    fetcher = AsyncMock(return_value=parsed(updated="2026-10-01 12:41:32"))
    result = await PypiRankingService(
        sessions, fetcher=fetcher, clock=lambda: NOW
    ).list_rankings()
    assert result.stale and result.period == "2026-09"
    assert "最近完整月份" in result.warnings[0]


@pytest.mark.anyio
async def test_older_upstream_cannot_replace_newer_snapshot(sessions):
    fetcher = AsyncMock(return_value=parsed())
    service = PypiRankingService(sessions, fetcher=fetcher, clock=lambda: NOW)
    result = await service.list_rankings()
    fetcher.return_value = parsed(updated="2026-10-01 12:41:32")
    old = await service.list_rankings(force_refresh=True)
    assert old.snapshot_id == result.snapshot_id and old.stale
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PypiSnapshot)) == 1


@pytest.mark.anyio
async def test_repeat_payload_deduplicated_and_revision_retained(sessions):
    fetcher = AsyncMock(return_value=parsed())
    service = PypiRankingService(sessions, fetcher=fetcher, clock=lambda: NOW)
    first = await service.list_rankings()
    assert (
        await service.list_rankings(force_refresh=True)
    ).snapshot_id == first.snapshot_id
    fetcher.return_value = parsed(updated="2026-11-01 07:00:00")
    revised = await service.list_rankings(force_refresh=True)
    assert revised.snapshot_id != first.snapshot_id and revised.period == first.period
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PypiSnapshot)) == 2


@pytest.mark.anyio
async def test_cross_worker_lease_prevents_duplicate_network_calls(sessions):
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetcher(**kwargs):
        # 租约必须已经提交；其他连接能够立即读到，不占用长事务。
        async with sessions() as session:
            state = await session.get(PypiRefreshState, 1)
            assert state.lease_token is not None
        entered.set()
        await release.wait()
        return parsed()

    first = PypiRankingService(sessions, fetcher=fetcher, clock=lambda: NOW)
    other_fetch = AsyncMock(return_value=parsed())
    second = PypiRankingService(sessions, fetcher=other_fetch, clock=lambda: NOW)
    task = asyncio.create_task(first.list_rankings())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(RankingUnavailable):
            await second.list_rankings()
        other_fetch.assert_not_awaited()
    finally:
        release.set()
        await task
    assert (await second.list_rankings()).period == "2026-10"
    other_fetch.assert_not_awaited()


@pytest.mark.anyio
async def test_expired_lease_cannot_overwrite_new_holder(sessions):
    clock = [NOW]
    service = PypiRankingService(sessions, clock=lambda: clock[0])
    old_token = await service._claim(clock[0], False)
    clock[0] += timedelta(seconds=61)
    new_token = await service._claim(clock[0], False)
    await service._finish(old_token, parsed=parsed())
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PypiSnapshot)) == 0
        assert (await session.get(PypiRefreshState, 1)).lease_token == new_token
    await service._finish(new_token, parsed=parsed())
    assert (await service.list_rankings()).period == "2026-10"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,body,content_type",
    [
        (302, b"private upstream text", "application/json"),
        (503, b"private upstream text", "application/json"),
        (200, b"{}", "text/html"),
        (200, b"x" * 2_000_001, "application/json"),
    ],
    ids=["redirect", "unavailable", "wrong-content-type", "too-large"],
)
async def test_source_network_rejects_redirect_errors_wrong_type_and_size(
    monkeypatch, status, body, content_type
):
    original = httpx.AsyncClient
    calls = []

    def make_client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False

        async def handler(request):
            calls.append(request)
            assert str(request.url) == SOURCE_URL
            assert "authorization" not in request.headers
            return httpx.Response(
                status,
                content=body,
                headers={
                    "content-type": content_type,
                    "location": "https://untrusted.invalid",
                },
            )

        return original(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(source.httpx, "AsyncClient", make_client)
    with pytest.raises(RankingUnavailable) as error:
        await fetch_snapshot(now=NOW)
    assert "private upstream text" not in str(error.value)
    assert len(calls) == 1


def test_public_api_needs_no_login_or_model_credentials_and_forwards_filters():
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock(spec=PypiRankingService)
    service.list_rankings.return_value = example_response()
    app.dependency_overrides[get_pypi_ranking_service] = lambda: service
    with TestClient(app) as client:
        result = client.get(
            "/rankings/pypi?category=web&projects=fastapi&projects=flask&limit=2"
        )
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json()["source"] == "hugovk/top-pypi-packages"
    service.list_rankings.assert_awaited_once_with(
        query="", category="web", projects=["fastapi", "flask"], limit=2
    )


@pytest.mark.parametrize(
    "path", ["?limit=0", "?limit=51", "?category=unknown", "?query=" + "x" * 101]
)
def test_invalid_public_query_does_not_call_service(path):
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock(spec=PypiRankingService)
    app.dependency_overrides[get_pypi_ranking_service] = lambda: service
    with TestClient(app) as client:
        assert client.get("/rankings/pypi" + path).status_code == 422
    service.list_rankings.assert_not_awaited()


def test_unavailable_api_has_safe_retryable_error():
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock(spec=PypiRankingService)
    service.list_rankings.side_effect = RankingUnavailable()
    app.dependency_overrides[get_pypi_ranking_service] = lambda: service
    with TestClient(app) as client:
        result = client.get("/rankings/pypi")
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "rankings_unavailable"
    assert (
        result.headers["cache-control"] == "no-store"
        and result.headers["retry-after"] == "60"
    )
