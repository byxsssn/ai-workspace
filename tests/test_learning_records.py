import asyncio
import json
import traceback
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx2
import pytest

from ai_workspace.learning import notion
from ai_workspace.learning.config import LearningSettings
from ai_workspace.learning.demo import (
    DEMO_FASTAPI_RECORD_ID,
    demo_learning_record,
    demo_learning_records,
)
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.notion import NotionLearningReader
from ai_workspace.learning.service import LearningService

TOKEN = "ntn-fabricated-records-secret"
SOURCE_ID = UUID("10000000-0000-4000-8000-000000000001")
PAGE_ID = UUID("20000000-0000-4000-8000-000000000001")
OTHER_ID = UUID("20000000-0000-4000-8000-000000000002")
QUERY_URL = f"https://api.notion.com/v1/data_sources/{SOURCE_ID}/query"


def page(record_id=PAGE_ID, *, parent_id=SOURCE_ID, title="FastAPI"):
    """完全虚构的 Notion 页面，不包含用户的真实记录。"""
    return {
        "object": "page",
        "id": str(record_id),
        "parent": {"type": "data_source_id", "data_source_id": str(parent_id)},
        "archived": False,
        "in_trash": False,
        "last_edited_time": "2026-11-01T08:00:00+08:00",
        "url": "https://untrusted.example/ignored",
        "properties": {
            "技术 / 主题": {"type": "title", "title": [{"plain_text": title}]},
            "类型": {"type": "select", "select": {"name": "后端框架"}},
            "求职优先级": {"type": "select", "select": {"name": "P0"}},
            "学习状态": {"type": "select", "select": {"name": "学习中"}},
            "当前进度": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "虚构进度"}],
            },
            "下一步": {"type": "rich_text", "rich_text": [{"plain_text": "虚构练习"}]},
            "掌握程度": {"type": "select", "select": {"name": "看过"}},
            "项目实践": {"type": "checkbox", "checkbox": False},
            "官方文档": {"type": "url", "url": "https://fastapi.tiangolo.com/"},
            "成果链接": {"type": "url", "url": None},
        },
    }


def query_response(items, *, has_more=False, cursor=None):
    return {
        "object": "list",
        "results": items,
        "has_more": has_more,
        "next_cursor": cursor,
    }


def markdown(record_id=PAGE_ID, *, truncated=False):
    return {
        "object": "page_markdown",
        "id": str(record_id),
        "markdown": "## 虚构笔记\n\n这是演示测试内容。",
        "truncated": truncated,
        "unknown_block_ids": [],
    }


def list_records(handler, *, fastapi_page_id=PAGE_ID):
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler),
            follow_redirects=True,
            headers={"Authorization": "Bearer unrelated-default"},
        ) as client:
            result = await NotionLearningReader(client).list_records(
                token=TOKEN, data_source_id=SOURCE_ID, fastapi_page_id=fastapi_page_id
            )
            assert client.headers["Authorization"] == "Bearer unrelated-default"
            return result

    return asyncio.run(run())


def read_record(handler):
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler)
        ) as client:
            return await NotionLearningReader(client).read_record(
                token=TOKEN,
                data_source_id=SOURCE_ID,
                page_id=PAGE_ID,
                fastapi_page_id=PAGE_ID,
            )

    return asyncio.run(run())


def test_list_follows_pagination_and_returns_only_properties():
    requests = []

    def handle(request):
        requests.append(request)
        payload = (
            query_response([page()], has_more=True, cursor="opaque-cursor")
            if len(requests) == 1
            else query_response([page(OTHER_ID)])
        )
        return httpx2.Response(200, json=payload)

    before = datetime.now(UTC)
    result = list_records(handle)
    assert [item.id for item in result.items] == [PAGE_ID, OTHER_ID]
    assert [item.topic_id for item in result.items] == ["fastapi", None]
    assert all(item.topic_name == "FastAPI" for item in result.items)
    assert result.items[0].category == "后端框架"
    assert result.items[0].priority == "P0"
    assert result.items[0].source_url == f"https://www.notion.so/{PAGE_ID.hex}"
    assert result.items[0].source_updated_at == datetime(2026, 11, 1, tzinfo=UTC)
    assert before <= result.fetched_at <= datetime.now(UTC)
    assert result.has_more is False and result.warnings == []
    assert "notes_markdown" not in result.model_dump_json()
    assert "notes_complete" not in result.model_dump_json()
    assert len(requests) == 2
    for request in requests:
        assert str(request.url) == QUERY_URL
        assert request.method == "POST"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["Notion-Version"] == "2026-03-11"
        assert json.loads(request.content)["result_type"] == "page"
        assert json.loads(request.content)["is_archived"] is False
    assert "start_cursor" not in json.loads(requests[0].content)
    assert json.loads(requests[1].content)["start_cursor"] == "opaque-cursor"


def test_topic_is_unknown_without_matching_configured_fastapi_page():
    result = list_records(
        lambda request: httpx2.Response(200, json=query_response([page()])),
        fastapi_page_id=None,
    )
    assert result.items[0].topic_id is None


def test_missing_optional_fields_remain_unknown():
    payload = page()
    payload["properties"] = {"技术 / 主题": payload["properties"]["技术 / 主题"]}
    del payload["last_edited_time"]
    result = list_records(
        lambda request: httpx2.Response(200, json=query_response([payload]))
    )
    item = result.items[0]
    assert item.category is None and item.priority is None
    assert item.project_practice is None and item.proficiency is None
    assert item.source_updated_at is None
    assert {"missing_category", "missing_priority", "missing_source_updated_at"} <= set(
        item.warnings
    )


@pytest.mark.parametrize("field", ["类型", "求职优先级", "技术 / 主题"])
def test_wrong_field_type_is_rejected(field):
    payload = page()
    payload["properties"][field] = {"type": "rich_text", "rich_text": []}
    with pytest.raises(LearningSourceError) as caught:
        list_records(
            lambda request: httpx2.Response(200, json=query_response([payload]))
        )
    assert caught.value.code == "source_schema_mismatch"


def test_list_rejects_page_from_another_data_source():
    with pytest.raises(LearningSourceError) as caught:
        list_records(
            lambda request: httpx2.Response(
                200, json=query_response([page(parent_id=uuid4())])
            )
        )
    assert caught.value.code == "invalid_source_response"


def test_detail_checks_parent_before_fetching_notes_and_preserves_incompleteness():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(
            200,
            json=markdown(truncated=True)
            if request.url.path.endswith("/markdown")
            else page(),
        )

    result = read_record(handle)
    assert [str(request.url) for request in requests] == [
        f"https://api.notion.com/v1/pages/{PAGE_ID}",
        f"https://api.notion.com/v1/pages/{PAGE_ID}/markdown",
    ]
    assert all(request.method == "GET" for request in requests)
    assert result.id == PAGE_ID and result.topic_id == "fastapi"
    assert result.notes_markdown.startswith("## 虚构笔记")
    assert result.notes_complete is False
    assert "notion_notes_truncated" in result.warnings


@pytest.mark.parametrize(
    "problem",
    [
        "other_source",
        "page_parent",
        "missing_parent",
        "archived",
        "in_trash",
        "wrong_id",
    ],
)
def test_out_of_scope_details_never_fetch_notes(problem):
    payload = page()
    if problem == "other_source":
        payload["parent"]["data_source_id"] = str(uuid4())
    elif problem == "page_parent":
        payload["parent"] = {"type": "page_id", "page_id": str(uuid4())}
    elif problem == "missing_parent":
        del payload["parent"]
    elif problem == "wrong_id":
        payload["id"] = str(OTHER_ID)
    else:
        payload[problem] = True
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(200, json=payload)

    with pytest.raises(LearningSourceError) as caught:
        read_record(handle)
    assert len(requests) == 1
    assert not requests[0].url.path.endswith("/markdown")
    if problem == "wrong_id":
        assert caught.value.code == "invalid_source_response"
    else:
        assert caught.value.code == "learning_record_not_found"
        assert caught.value.status_code == 404


@pytest.mark.parametrize(
    "mutation",
    [
        {"object": "page"},
        {"has_more": 1},
        {"results": {}},
        {"has_more": True, "next_cursor": None},
        {"has_more": True, "next_cursor": ""},
        {"has_more": False, "next_cursor": "unexpected"},
        {"request_status": {"type": "unknown"}},
    ],
)
def test_malformed_pagination_is_rejected(mutation):
    payload = {**query_response([]), **mutation}
    with pytest.raises(LearningSourceError) as caught:
        list_records(lambda request: httpx2.Response(200, json=payload))
    assert caught.value.code == "invalid_source_response"


def test_repeated_cursor_cannot_loop_forever():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(
            200, json=query_response([], has_more=True, cursor="same")
        )

    with pytest.raises(LearningSourceError) as caught:
        list_records(handle)
    assert caught.value.code == "invalid_source_response"
    assert len(requests) == 2


def test_duplicate_records_are_rejected():
    with pytest.raises(LearningSourceError) as caught:
        list_records(
            lambda request: httpx2.Response(200, json=query_response([page(), page()]))
        )
    assert caught.value.code == "invalid_source_response"


def test_record_limit_is_explicit_and_does_not_fetch_an_extra_page(monkeypatch):
    monkeypatch.setattr(notion, "_MAX_RECORDS", 2)
    monkeypatch.setattr(notion, "_QUERY_PAGE_SIZE", 1)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(
            200,
            json=query_response(
                [page(PAGE_ID if len(requests) == 1 else OTHER_ID)],
                has_more=True,
                cursor=f"cursor-{len(requests)}",
            ),
        )

    result = list_records(handle)
    assert len(requests) == len(result.items) == 2
    assert result.has_more is True
    assert result.warnings == ["record_limit_reached"]


def test_empty_pages_are_bounded(monkeypatch):
    monkeypatch.setattr(notion, "_MAX_QUERY_PAGES", 2)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(
            200,
            json=query_response([], has_more=True, cursor=f"cursor-{len(requests)}"),
        )

    result = list_records(handle)
    assert len(requests) == 2 and result.items == []
    assert result.has_more is True
    assert result.warnings == ["notion_page_limit_reached"]


def test_upstream_query_cap_is_not_reported_as_a_complete_list():
    payload = query_response([page()])
    payload["request_status"] = {
        "type": "incomplete",
        "incomplete_reason": "private-upstream-detail",
    }
    result = list_records(lambda request: httpx2.Response(200, json=payload))
    assert result.has_more is True
    assert result.warnings == ["notion_query_incomplete"]
    assert "private-upstream-detail" not in result.model_dump_json()


def test_query_bytes_are_bounded_across_pages(monkeypatch):
    payloads = [
        json.dumps(query_response([], has_more=True, cursor="next")).encode(),
        json.dumps(query_response([])).encode(),
    ]
    monkeypatch.setattr(notion, "_MAX_QUERY_BYTES", sum(map(len, payloads)) - 1)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(
            200,
            content=payloads[len(requests) - 1],
            headers={"content-type": "application/json"},
        )

    with pytest.raises(LearningSourceError) as caught:
        list_records(handle)
    assert caught.value.code == "source_too_large"
    assert len(requests) == 2


@pytest.mark.parametrize("operation", [list_records, read_record])
def test_entire_read_has_a_timeout(monkeypatch, operation):
    monkeypatch.setattr(notion, "_CATALOG_TIMEOUT_SECONDS", 0.01)

    async def handle(request):
        await asyncio.sleep(1)
        pytest.fail("The overall read deadline must cancel the request")

    with pytest.raises(LearningSourceError) as caught:
        operation(handle)
    assert caught.value.code == "source_timeout" and caught.value.status_code == 504


@pytest.mark.parametrize(
    ("status", "code", "outward"),
    [
        (401, "source_unauthorized", 502),
        (403, "source_forbidden", 502),
        (404, "source_not_found", 502),
        (429, "source_rate_limited", 503),
        (302, "source_redirect", 502),
        (500, "source_unavailable", 502),
    ],
)
def test_query_failures_do_not_retry_redirect_or_leak(status, code, outward):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(
            status,
            text=TOKEN,
            headers={"location": "https://outside.example", "Retry-After": "3"},
        )

    with pytest.raises(LearningSourceError) as caught:
        list_records(handle)
    assert len(requests) == 1
    assert caught.value.code == code and caught.value.status_code == outward
    assert TOKEN not in "".join(traceback.format_exception(caught.value))
    if status == 429:
        assert caught.value.retry_after == "3"


def test_not_found_detail_is_a_controlled_404():
    with pytest.raises(LearningSourceError) as caught:
        read_record(lambda request: httpx2.Response(404, text=TOKEN))
    assert caught.value.code == "learning_record_not_found"
    assert caught.value.status_code == 404


@pytest.mark.parametrize("escaped", [False, True])
def test_secret_echo_is_rejected_even_in_unused_fields(escaped):
    payload = query_response([page()])
    payload["unused_private_field"] = TOKEN
    content = json.dumps(payload).encode()
    if escaped:
        content = content.replace(
            TOKEN.encode(), "".join(f"\\u{ord(c):04x}" for c in TOKEN).encode()
        )
    with pytest.raises(LearningSourceError) as caught:
        list_records(
            lambda request: httpx2.Response(
                200, content=content, headers={"content-type": "application/json"}
            )
        )
    assert caught.value.code == "invalid_source_response"
    assert TOKEN not in "".join(traceback.format_exception(caught.value))


def configured_settings(owner, *, with_source=True):
    return LearningSettings(
        _env_file=None,
        notion_api_token=TOKEN,
        notion_owner_user_id=owner,
        notion_fastapi_page_id=PAGE_ID,
        notion_learning_data_source_id=SOURCE_ID if with_source else None,
    )


def test_service_reuses_owner_scope_for_list_and_detail():
    async def run():
        owner = uuid4()
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: pytest.fail("Only mocked reader calls expected")
            )
        ) as client:
            service = LearningService(configured_settings(owner), client)
            service.notion.list_records = AsyncMock(
                return_value=demo_learning_records()
            )
            service.notion.read_record = AsyncMock(
                return_value=demo_learning_record(DEMO_FASTAPI_RECORD_ID)
            )
            for operation in (
                lambda user: service.list_records(user_id=user, source="notion"),
                lambda user: service.read_record(
                    user_id=user, source="notion", record_id=PAGE_ID
                ),
            ):
                with pytest.raises(LearningSourceError) as caught:
                    await operation(uuid4())
                assert caught.value.code == "notion_not_available"
                assert caught.value.status_code == 404
            service.notion.list_records.assert_not_called()
            service.notion.read_record.assert_not_called()
            await service.list_records(user_id=owner, source="notion")
            await service.read_record(user_id=owner, source="notion", record_id=PAGE_ID)
            service.notion.list_records.assert_awaited_once_with(
                token=TOKEN, data_source_id=SOURCE_ID, fastapi_page_id=PAGE_ID
            )
            service.notion.read_record.assert_awaited_once_with(
                token=TOKEN,
                data_source_id=SOURCE_ID,
                page_id=PAGE_ID,
                fastapi_page_id=PAGE_ID,
            )

    asyncio.run(run())


@pytest.mark.parametrize("connection", ["none", "no_source"])
def test_missing_configuration_never_falls_back_to_demo(connection):
    async def run():
        owner = uuid4()
        settings = (
            configured_settings(owner, with_source=False)
            if connection == "no_source"
            else LearningSettings(_env_file=None)
        )
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: pytest.fail("No HTTP allowed")
            )
        ) as client:
            service = LearningService(settings, client)
            for operation in (
                service.list_records(user_id=owner, source="notion"),
                service.read_record(user_id=owner, source="notion", record_id=PAGE_ID),
            ):
                with pytest.raises(LearningSourceError) as caught:
                    await operation
                assert caught.value.code == (
                    "notion_catalog_not_configured"
                    if connection == "no_source"
                    else "notion_not_configured"
                )
                assert caught.value.status_code == 503

    asyncio.run(run())


def test_demo_records_are_stable_explicit_and_need_no_notion():
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: pytest.fail("Demo must not make requests")
            )
        ) as client:
            service = LearningService(LearningSettings(_env_file=None), client)
            result = await service.list_records(user_id=uuid4(), source="demo")
            assert result.source == "demo" and not result.has_more
            assert len(result.items) == 3
            assert result.items[0].id == DEMO_FASTAPI_RECORD_ID
            assert [item.topic_id for item in result.items] == ["fastapi", None, None]
            for item in result.items:
                detail = await service.read_record(
                    user_id=uuid4(), source="demo", record_id=item.id
                )
                assert detail.id == item.id
                assert detail.notes_complete
                assert "虚构" in detail.notes_markdown
                assert detail.source_url is None and detail.source_updated_at is None
                assert "demo_data_not_personal_progress" in detail.warnings
            with pytest.raises(LearningSourceError) as caught:
                await service.read_record(
                    user_id=uuid4(), source="demo", record_id=uuid4()
                )
            assert caught.value.code == "learning_record_not_found"

    asyncio.run(run())


def test_concurrent_queries_do_not_share_credentials():
    requests = []

    async def run():
        both_started = asyncio.Event()

        async def handle(request):
            requests.append(request)
            if len(requests) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=2)
            return httpx2.Response(200, json=query_response([page()]))

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handle),
            headers={"Authorization": "Bearer unrelated-default"},
        ) as client:
            reader = NotionLearningReader(client)
            results = await asyncio.gather(
                reader.list_records(
                    token="fictional-owner-a",
                    data_source_id=SOURCE_ID,
                    fastapi_page_id=PAGE_ID,
                ),
                reader.list_records(
                    token="fictional-owner-b",
                    data_source_id=SOURCE_ID,
                    fastapi_page_id=PAGE_ID,
                ),
            )
            assert all(result.items[0].id == PAGE_ID for result in results)
            assert client.headers["Authorization"] == "Bearer unrelated-default"

    asyncio.run(run())
    assert {request.headers["Authorization"] for request in requests} == {
        "Bearer fictional-owner-a",
        "Bearer fictional-owner-b",
    }
