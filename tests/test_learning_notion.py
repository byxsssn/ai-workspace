import asyncio
import json
import logging
import traceback
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2
import pytest

from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.notion import NotionLearningReader

TOKEN = "ntn-fictional-test-secret-must-not-appear"
PAGE_ID = UUID("83b77246-9016-4ad9-87b3-ab4226e8f777")
ENDPOINT = f"https://api.notion.com/v1/pages/{PAGE_ID}"
UPSTREAM_DETAIL = "private-upstream-error-details"


def page_payload() -> dict:
    """完全虚构的学习记录，不读取个人笔记或本地密钥。"""
    return {
        "object": "page",
        "id": str(PAGE_ID),
        "archived": False,
        "in_trash": False,
        "last_edited_time": "2026-10-30T09:15:00+08:00",
        "url": "https://untrusted.example.test/ignored",
        "properties": {
            "技术 / 主题": {
                "type": "title",
                "title": [{"plain_text": "Fast"}, {"plain_text": "API"}],
            },
            "学习状态": {"type": "select", "select": {"name": "学习中"}},
            "当前进度": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "虚构进度："}, {"plain_text": "已读测试"}],
            },
            "下一步": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "完成一个虚构练习"}],
            },
            "掌握程度": {"type": "select", "select": {"name": "看过"}},
            "项目实践": {"type": "checkbox", "checkbox": False},
            "官方文档": {"type": "url", "url": "https://fastapi.tiangolo.com/"},
            "成果链接": {"type": "url", "url": None},
        },
    }


def markdown_payload() -> dict:
    return {
        "object": "page_markdown",
        "id": str(PAGE_ID),
        "markdown": "## 虚构笔记\n\n  保留空白。\n",
        "truncated": False,
        "unknown_block_ids": [],
        "warnings": [],
    }


def read(transport: httpx2.MockTransport, token: str = TOKEN):
    async def run():
        async with httpx2.AsyncClient(
            transport=transport,
            follow_redirects=True,
            headers={"Authorization": "Bearer unrelated-client-default"},
        ) as client:
            result = await NotionLearningReader(client).read(
                token=token, page_id=PAGE_ID
            )
            assert client.headers["Authorization"] == "Bearer unrelated-client-default"
            return result

    return asyncio.run(run())


def transport_for(page: object, markdown: object) -> httpx2.MockTransport:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, json=markdown if request.url.path.endswith("/markdown") else page
        )

    return httpx2.MockTransport(handle)


def assert_safe_error(error: LearningSourceError, caplog: pytest.LogCaptureFixture):
    for value in (TOKEN, UPSTREAM_DETAIL):
        assert value not in str(error)
        assert value not in repr(error)
        assert value not in "".join(traceback.format_exception(error))
        assert value not in caplog.text
    assert error.__cause__ is None


def test_reads_properties_and_markdown_with_scoped_headers():
    requests = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json=(
                markdown_payload()
                if request.url.path.endswith("/markdown")
                else page_payload()
            ),
        )

    before = datetime.now(UTC)
    result = read(httpx2.MockTransport(handle))
    assert [str(request.url) for request in requests] == [
        ENDPOINT,
        f"{ENDPOINT}/markdown",
    ]
    assert all(request.method == "GET" for request in requests)
    assert all(
        request.headers["Authorization"] == f"Bearer {TOKEN}" for request in requests
    )
    assert all(
        request.headers["Notion-Version"] == "2026-03-11" for request in requests
    )
    assert result.topic_id == "fastapi"
    assert result.topic_name == "FastAPI"
    assert result.source == "notion"
    assert result.source_url == f"https://www.notion.so/{PAGE_ID.hex}"
    assert result.source_updated_at == datetime(2026, 10, 30, 1, 15, tzinfo=UTC)
    assert before <= result.fetched_at <= datetime.now(UTC)
    assert result.learning_status == "学习中"
    assert result.current_progress == "虚构进度：已读测试"
    assert result.next_step == "完成一个虚构练习"
    assert result.proficiency == "看过"
    assert result.project_practice is False
    assert result.official_docs_url == "https://fastapi.tiangolo.com/"
    assert result.evidence_url is None
    assert result.notes_markdown == markdown_payload()["markdown"]
    assert result.notes_complete is True
    assert result.warnings == []


def test_missing_optional_fields_remain_unknown_with_warnings():
    page = page_payload()
    page["properties"] = {"技术 / 主题": page["properties"]["技术 / 主题"]}
    del page["last_edited_time"]
    content = markdown_payload()
    del content["warnings"]
    result = read(transport_for(page, content))
    fields = (
        "learning_status",
        "current_progress",
        "next_step",
        "proficiency",
        "project_practice",
        "official_docs_url",
        "evidence_url",
        "source_updated_at",
    )
    assert all(getattr(result, field) is None for field in fields)
    assert set(result.warnings) == {f"missing_{field}" for field in fields}
    assert result.notes_complete is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("学习状态", {"type": "select", "select": None}),
        ("当前进度", {"type": "rich_text", "rich_text": []}),
    ],
)
def test_explicitly_empty_properties_are_valid(field, value):
    page = page_payload()
    page["properties"][field] = value
    result = read(transport_for(page, markdown_payload()))
    assert result.warnings == []
    assert (
        result.learning_status if field == "学习状态" else result.current_progress
    ) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("技术 / 主题", {"type": "title", "title": []}),
        ("学习状态", {"type": "status", "status": {"name": "学习中"}}),
        ("下一步", {"type": "rich_text", "rich_text": [{"plain_text": 123}]}),
        ("掌握程度", {"type": "select", "select": {"name": 123}}),
        ("项目实践", {"type": "checkbox", "checkbox": 1}),
        ("官方文档", {"type": "url", "url": ["https://example.test/"]}),
        ("成果链接", None),
    ],
)
def test_invalid_property_types_are_reported_as_schema_mismatch(field, value):
    page = page_payload()
    page["properties"][field] = value
    with pytest.raises(LearningSourceError) as caught:
        read(transport_for(page, markdown_payload()))
    assert caught.value.code == "source_schema_mismatch"


def test_missing_title_is_a_schema_error_before_reading_content():
    calls = []
    page = page_payload()
    del page["properties"]["技术 / 主题"]

    def handle(request):
        calls.append(request)
        return httpx2.Response(200, json=page)

    with pytest.raises(LearningSourceError) as caught:
        read(httpx2.MockTransport(handle))
    assert caught.value.code == "source_schema_mismatch"
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("object", "database"),
        ("id", str(uuid4())),
        ("id", "not-a-uuid"),
        ("archived", True),
        ("in_trash", True),
        ("is_archived", True),
        ("archived", "false"),
        ("last_edited_time", "2026-10-30T09:15:00"),
        ("last_edited_time", "not-a-timestamp"),
        ("last_edited_time", "0001-01-01T00:00:00+08:00"),
        ("last_edited_time", 0),
    ],
)
def test_invalid_page_metadata_is_rejected(field, value):
    page = page_payload()
    page[field] = value
    with pytest.raises(LearningSourceError) as caught:
        read(transport_for(page, markdown_payload()))
    assert caught.value.code == "invalid_source_response"


@pytest.mark.parametrize(
    ("change", "expected_warning"),
    [
        ({"truncated": True}, "notion_notes_truncated"),
        ({"unknown_block_ids": [str(uuid4())]}, "notion_notes_unknown_blocks"),
        (
            {"warnings": [{"code": "unknown_warning", "message": UPSTREAM_DETAIL}]},
            "notion_notes_warnings",
        ),
        (
            {"markdown": '<unknown url="https://example.test/"/>'},
            "notion_notes_unknown_markup",
        ),
    ],
)
def test_partial_content_is_preserved_and_marked_incomplete(change, expected_warning):
    content = markdown_payload() | change
    result = read(transport_for(page_payload(), content))
    assert result.notes_complete is False
    assert result.notes_markdown == content["markdown"]
    assert result.warnings == [expected_warning]
    assert UPSTREAM_DETAIL not in result.model_dump_json()


@pytest.mark.parametrize("missing", ["truncated", "unknown_block_ids"])
def test_missing_completeness_metadata_is_not_assumed_complete(missing):
    content = markdown_payload()
    del content[missing]
    with pytest.raises(LearningSourceError) as caught:
        read(transport_for(page_payload(), content))
    assert caught.value.code == "invalid_source_response"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("object", "page"),
        ("id", str(uuid4())),
        ("markdown", None),
        ("truncated", 0),
        ("unknown_block_ids", {}),
        ("unknown_block_ids", [None]),
        ("warnings", "warning"),
        ("warnings", [{"code": "warning", "message": 1}]),
    ],
)
def test_invalid_markdown_response_is_rejected(field, value):
    content = markdown_payload() | {field: value}
    with pytest.raises(LearningSourceError) as caught:
        read(transport_for(page_payload(), content))
    assert caught.value.code == "invalid_source_response"


@pytest.mark.parametrize("body", [b"not-json", b"\xff", b"[]", b"null"])
def test_invalid_json_is_reported_without_upstream_details(body, caplog):
    def handle(request):
        return httpx2.Response(
            200, content=body, headers={"content-type": "application/json"}
        )

    with pytest.raises(LearningSourceError) as caught:
        read(httpx2.MockTransport(handle))
    assert caught.value.code == "invalid_source_response"
    assert_safe_error(caught.value, caplog)


@pytest.mark.parametrize(
    ("status", "code", "expected_status"),
    [
        (401, "source_unauthorized", 502),
        (403, "source_forbidden", 502),
        (404, "source_not_found", 502),
        (429, "source_rate_limited", 503),
    ],
)
def test_upstream_access_and_rate_errors_are_safe(
    status, code, expected_status, caplog
):
    caplog.set_level(logging.DEBUG)

    def handle(request):
        return httpx2.Response(
            status,
            json={"message": f"{TOKEN} {UPSTREAM_DETAIL}"},
            headers={"retry-after": "17"},
        )

    with pytest.raises(LearningSourceError) as caught:
        read(httpx2.MockTransport(handle))
    assert caught.value.code == code
    assert caught.value.status_code == expected_status
    assert caught.value.retry_after == ("17" if status == 429 else None)
    assert_safe_error(caught.value, caplog)


def test_does_not_follow_redirects_even_if_client_enables_them(caplog):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(
            307, headers={"location": f"https://example.test/{TOKEN}"}
        )

    with pytest.raises(LearningSourceError) as caught:
        read(httpx2.MockTransport(handle))
    assert caught.value.code == "source_redirect"
    assert len(calls) == 1
    assert_safe_error(caught.value, caplog)


@pytest.mark.parametrize("escaped", [False, True])
def test_rejects_raw_and_json_escaped_credential_echo(escaped, caplog):
    page = page_payload()
    page["properties"]["技术 / 主题"]["title"][0]["plain_text"] = TOKEN
    content = json.dumps(page).encode()
    if escaped:
        content = content.replace(
            TOKEN.encode(), "".join(f"\\u{ord(c):04x}" for c in TOKEN).encode()
        )

    def handle(request):
        return httpx2.Response(
            200, content=content, headers={"content-type": "application/json"}
        )

    with pytest.raises(LearningSourceError) as caught:
        read(httpx2.MockTransport(handle))
    assert caught.value.code == "invalid_source_response"
    assert_safe_error(caught.value, caplog)


def test_concurrent_reads_keep_credentials_isolated():
    second_token = "ntn-second-fictional-test-token"
    second_page_id = UUID("b5b77f33-3876-40a6-896c-c99565852436")
    expected = {str(PAGE_ID): TOKEN, str(second_page_id): second_token}
    calls = []

    async def handle(request):
        identity = request.url.path.split("/")[3]
        calls.append((identity, request.headers["Authorization"]))
        assert request.headers["Authorization"] == f"Bearer {expected[identity]}"
        await asyncio.sleep(0)
        payload = (
            markdown_payload()
            if request.url.path.endswith("/markdown")
            else page_payload()
        )
        payload["id"] = identity
        return httpx2.Response(200, json=payload)

    async def run():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
            reader = NotionLearningReader(client)
            results = await asyncio.gather(
                reader.read(token=TOKEN, page_id=PAGE_ID),
                reader.read(token=second_token, page_id=second_page_id),
            )
            assert "Authorization" not in client.headers
            assert len({result.source_url for result in results}) == 2

    asyncio.run(run())
    assert len(calls) == 4
