import asyncio
import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest
from pydantic import ValidationError

from ai_workspace.learning.config import LearningSettings
from ai_workspace.learning.documents import SECTIONS
from ai_workspace.learning.errors import ERROR_MESSAGES, LearningSourceError
from ai_workspace.learning.models import LearningState, OfficialDocument
from ai_workspace.learning.service import LearningService
from ai_workspace.learning.tools import ToolSession


def _settings(**kwargs):
    values = {
        "notion_api_token": None,
        "notion_owner_user_id": None,
        "notion_fastapi_page_id": None,
    }
    return LearningSettings(_env_file=None, **(values | kwargs))


def _tools(session):
    return {item.name: item for item in session.build_tools()}


def _no_network(request):
    pytest.fail(f"Unexpected source request: {request.url}")


def _state(**kwargs):
    values = {
        "topic_name": "FastAPI",
        "source": "notion",
        "source_url": f"https://www.notion.so/{uuid4().hex}",
        "fetched_at": datetime.now(UTC),
        "notes_markdown": "## 笔记\n\n尚未完成实践。",
        "notes_complete": True,
    }
    return LearningState(**(values | kwargs))


def _document(content):
    return OfficialDocument(
        section_id="testing",
        title="Testing",
        url=SECTIONS[0].url,
        content_markdown=content,
        fetched_at=datetime.now(UTC),
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
    )


def test_tool_schemas_reject_identity_source_and_arbitrary_urls():
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(_no_network)
        ) as client:
            session = ToolSession(LearningService(_settings(), client), uuid4(), "demo")
            tools = _tools(session)
            assert set(tools) == {"get_learning_state", "read_official_doc"}
            for name, item in tools.items():
                schema = item.args_schema.model_json_schema()
                assert schema["additionalProperties"] is False
                params = (
                    {} if name == "get_learning_state" else {"section_id": "testing"}
                )
                assert set(schema["properties"]) == set(params)
                for field in ("user_id", "page_id", "url", "source", "api_key"):
                    with pytest.raises(ValidationError):
                        await item.ainvoke(params | {field: "cannot-override"})
            document_tool = tools["read_official_doc"]
            schema = document_tool.args_schema.model_json_schema()
            section_schema = schema["properties"]["section_id"]
            if "$ref" in section_schema:
                section_schema = schema["$defs"][section_schema["$ref"].split("/")[-1]]
            assert section_schema["enum"] == [section.id for section in SECTIONS]
            for invalid in ("https://example.com/", "../testing", "unknown", 1):
                with pytest.raises(ValidationError):
                    await document_tool.ainvoke({"section_id": invalid})
            with pytest.raises(ValidationError):
                await tools["get_learning_state"].ainvoke(
                    {
                        "type": "tool_call",
                        "name": "get_learning_state",
                        "id": "call-injected",
                        "args": {"source": "notion"},
                    }
                )
            assert session.executions == []
            assert session.citations == {}
            assert session.state_read is False

    asyncio.run(run())


def test_tool_call_returns_json_content_for_the_agent():
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(_no_network)
        ) as client:
            session = ToolSession(LearningService(_settings(), client), uuid4(), "demo")
            message = await _tools(session)["get_learning_state"].ainvoke(
                {
                    "type": "tool_call",
                    "name": "get_learning_state",
                    "id": "call-state",
                    "args": {},
                }
            )
            result = json.loads(message.content)
            assert message.tool_call_id == "call-state"
            assert result["status"] == "success"
            assert result["source_id"] == "learning-state"
            assert result["source"] == "demo"

    asyncio.run(run())


@pytest.mark.parametrize("section", SECTIONS, ids=lambda section: section.id)
def test_document_tool_reuses_service_and_reader(section):
    async def run():
        requests = []

        def handler(request):
            requests.append(request)
            return httpx2.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<main><article class="md-content__inner"><h1>Testing</h1>'
                    '<pre><code class="language-python">assert 1 == 1\n</code></pre>'
                    "</article></main>"
                ),
            )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler)
        ) as client:
            session = ToolSession(LearningService(_settings(), client), uuid4(), "demo")
            result = await _tools(session)["read_official_doc"].ainvoke(
                {"section_id": section.id}
            )
            assert [str(request.url) for request in requests] == [section.url]
            assert result["status"] == "success"
            assert result["source_id"] == f"doc:{section.id}"
            assert result["source"] == "official_document"
            assert "# Testing" in result["content_markdown"]
            assert "assert 1 == 1" in result["content_markdown"]
            assert result["content_complete"] is True
            assert result["truncated"] is False
            assert result["version"] is None
            json.dumps(result)
            citation = session.citations[result["source_id"]]
            assert citation.url == section.url
            assert citation.content_sha256 == result["content_sha256"]
            assert citation.truncated is False
            assert session.state_read is False
            assert session.executions[0].model_dump() == {
                "tool": "read_official_doc",
                "section_id": section.id,
                "status": "success",
                "error_code": None,
                "cached": False,
            }

    asyncio.run(run())


def test_demo_state_is_explicit_and_does_not_read_notion():
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(_no_network)
        ) as client:
            service = LearningService(_settings(), client)
            service.notion.read = AsyncMock()
            session = ToolSession(service, uuid4(), "demo")
            result = await _tools(session)["get_learning_state"].ainvoke({})
            assert result["source_id"] == "learning-state"
            assert result["source"] == "demo"
            assert "demo_data_not_personal_progress" in result["warnings"]
            assert "虚构" in result["notes_markdown"]
            assert result["notes_complete"] is True
            assert session.state_read is True
            assert session.citations["learning-state"].url is None
            assert session.citations["learning-state"].kind == "learning_record"
            service.notion.read.assert_not_awaited()
            json.dumps(result)

    asyncio.run(run())


def test_concurrent_reads_are_cached_and_return_independent_values():
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(_no_network)
        ) as client:
            service = LearningService(_settings(), client)
            service.read_state = AsyncMock(wraps=service.read_state)
            session = ToolSession(service, uuid4(), "demo")
            get_state = _tools(session)["get_learning_state"]
            first, second = await asyncio.gather(
                get_state.ainvoke({}), get_state.ainvoke({})
            )
            service.read_state.assert_awaited_once_with(
                user_id=session.user_id, source="demo"
            )
            assert first == second
            assert [item.cached for item in session.executions] == [False, True]
            first["warnings"].append("mutated")
            first["notes_markdown"] = "mutated"
            third = await get_state.ainvoke({})
            assert third == second
            assert "mutated" not in third["warnings"]

    asyncio.run(run())


def test_document_cache_is_per_section():
    async def run():
        requests = []

        async def handler(request):
            requests.append(str(request.url))
            await asyncio.sleep(0)
            return httpx2.Response(
                200,
                headers={"content-type": "text/html"},
                text="<main><article><h1>Official content</h1></article></main>",
            )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler)
        ) as client:
            session = ToolSession(LearningService(_settings(), client), uuid4(), "demo")
            read_doc = _tools(session)["read_official_doc"]
            results = await asyncio.gather(
                read_doc.ainvoke({"section_id": "testing"}),
                read_doc.ainvoke({"section_id": "testing"}),
                read_doc.ainvoke({"section_id": "async-tests"}),
            )
            assert requests == [SECTIONS[0].url, SECTIONS[1].url]
            assert results[0] == results[1]
            assert set(session.citations) == {"doc:testing", "doc:async-tests"}
            assert [item.cached for item in session.executions] == [False, True, False]

    asyncio.run(run())


@pytest.mark.parametrize("size,limited", [(18_000, False), (18_001, True)])
def test_document_limit_keeps_full_source_hash_and_registry_url(size, limited):
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(_no_network)
        ) as client:
            content = "测" * size
            document = _document(content).model_copy(
                update={"url": "https://untrusted.invalid/", "title": "untrusted title"}
            )
            service = LearningService(_settings(), client)
            service.read_document = AsyncMock(return_value=document)
            session = ToolSession(service, uuid4(), "demo")
            result = await _tools(session)["read_official_doc"].ainvoke(
                {"section_id": "testing"}
            )
            assert len(result["content_markdown"]) == 18_000
            assert result["content_complete"] is (not limited)
            assert result["truncated"] is limited
            assert bool(result["warnings"]) is limited
            citation = session.citations["doc:testing"]
            assert citation.truncated is limited
            assert (
                citation.content_sha256 == hashlib.sha256(content.encode()).hexdigest()
            )
            assert citation.url == result["url"] == SECTIONS[0].url
            assert citation.title == result["title"] == SECTIONS[0].title

    asyncio.run(run())


@pytest.mark.parametrize(
    "size,source_complete,complete",
    [(8_000, True, True), (8_001, True, False), (20, False, False)],
)
def test_note_limit_combines_source_completeness(size, source_complete, complete):
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(_no_network)
        ) as client:
            state = _state(
                notes_markdown="学" * size,
                notes_complete=source_complete,
                warnings=[] if source_complete else ["unsupported_blocks"],
            )
            service = LearningService(_settings(), client)
            service.read_state = AsyncMock(return_value=state)
            session = ToolSession(service, uuid4(), "notion")
            result = await _tools(session)["get_learning_state"].ainvoke({})
            assert len(result["notes_markdown"]) == min(size, 8_000)
            assert result["notes_complete"] is complete
            assert result["truncated"] is (not complete)
            assert session.citations["learning-state"].truncated is (not complete)
            assert ("notes_truncated_for_model" in result["warnings"]) is (size > 8_000)
            if not source_complete:
                assert "unsupported_blocks" in result["warnings"]
            assert len(state.notes_markdown) == size
            assert "notes_truncated_for_model" not in state.warnings

    asyncio.run(run())


def test_notion_fields_do_not_become_trusted_document_citations():
    async def run():
        owner, page = uuid4(), uuid4()
        token = "fake-notion-token"
        settings = _settings(
            notion_api_token=token,
            notion_owner_user_id=owner,
            notion_fastapi_page_id=page,
        )
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(_no_network)
        ) as client:
            service = LearningService(settings, client)
            state = _state(
                source_url=f"https://www.notion.so/{page.hex}",
                official_docs_url="https://untrusted.invalid/docs",
                evidence_url="https://untrusted.invalid/proof",
            )
            service.notion.read = AsyncMock(return_value=state)
            session = ToolSession(service, owner, "notion")
            result = await _tools(session)["get_learning_state"].ainvoke({})
            service.notion.read.assert_awaited_once_with(token=token, page_id=page)
            assert "official_docs_url" not in result
            assert "evidence_url" not in result
            assert result["unverified_record_links"] == {
                "official_docs_url": state.official_docs_url,
                "evidence_url": state.evidence_url,
            }
            assert set(session.citations) == {"learning-state"}
            assert session.citations["learning-state"].url == state.source_url
            assert token not in json.dumps(result)
            assert str(owner) not in json.dumps(result)

    asyncio.run(run())


@pytest.mark.parametrize(
    "tool_name,params",
    [("get_learning_state", {}), ("read_official_doc", {"section_id": "testing"})],
)
def test_source_errors_are_cached_without_citations(tool_name, params):
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(_no_network)
        ) as client:
            service = LearningService(_settings(), client)
            read = AsyncMock(side_effect=LearningSourceError("source_forbidden"))
            if tool_name == "get_learning_state":
                service.read_state = read
            else:
                service.read_document = read
            session = ToolSession(service, uuid4(), "notion")
            item = _tools(session)[tool_name]
            first = await item.ainvoke(params)
            second = await item.ainvoke(params)
            assert (
                first
                == second
                == {
                    "status": "error",
                    "error": {
                        "code": "source_forbidden",
                        "message": ERROR_MESSAGES["source_forbidden"],
                    },
                }
            )
            assert read.await_count == 1
            assert session.citations == {}
            assert session.state_read is False
            assert [item.status for item in session.executions] == ["error", "error"]
            assert [item.cached for item in session.executions] == [False, True]
            assert all(
                item.error_code == "source_forbidden" for item in session.executions
            )

    asyncio.run(run())


def test_unexpected_errors_do_not_expose_exception_text():
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(_no_network)
        ) as client:
            service = LearningService(_settings(), client)
            service.read_state = AsyncMock(
                side_effect=RuntimeError("secret-token-body")
            )
            session = ToolSession(service, uuid4(), "notion")
            result = await _tools(session)["get_learning_state"].ainvoke({})
            assert result["error"]["code"] == "source_unavailable"
            assert "secret-token-body" not in json.dumps(result)
            assert "secret-token-body" not in repr(session.executions)
            assert session.citations == {}
            assert session.state_read is False

    asyncio.run(run())


def test_call_limit_includes_cached_results_and_prevents_more_reads():
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(_no_network)
        ) as client:
            service = LearningService(_settings(), client)
            service.read_state = AsyncMock(wraps=service.read_state)
            service.read_document = AsyncMock()
            session = ToolSession(service, uuid4(), "demo")
            tools = _tools(session)
            for _ in range(6):
                result = await tools["get_learning_state"].ainvoke({})
                assert result["status"] == "success"
            over_limit = await tools["read_official_doc"].ainvoke(
                {"section_id": "testing"}
            )
            cached_over_limit = await tools["get_learning_state"].ainvoke({})
            assert over_limit == cached_over_limit
            assert over_limit["error"]["code"] == "tool_call_limit"
            service.read_state.assert_awaited_once()
            service.read_document.assert_not_awaited()
            assert set(session.citations) == {"learning-state"}
            assert len(session.executions) == 8
            assert all(item.status == "error" for item in session.executions[-2:])
            assert all(item.cached is False for item in session.executions[-2:])

    asyncio.run(run())


def test_sessions_do_not_share_private_state_or_cache():
    async def run():
        owner = uuid4()
        settings = _settings(
            notion_api_token="fake-notion-token",
            notion_owner_user_id=owner,
            notion_fastapi_page_id=uuid4(),
        )
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(_no_network)
        ) as client:
            service = LearningService(settings, client)
            service.notion.read = AsyncMock(
                return_value=_state(notes_markdown="私人笔记")
            )
            owner_session = ToolSession(service, owner, "notion")
            other_session = ToolSession(service, uuid4(), "notion")
            demo_session = ToolSession(service, owner, "demo")
            owner_tool = _tools(owner_session)["get_learning_state"]
            other_tool = _tools(other_session)["get_learning_state"]
            demo_tool = _tools(demo_session)["get_learning_state"]
            owner_result, other_result, demo_result = await asyncio.gather(
                owner_tool.ainvoke({}), other_tool.ainvoke({}), demo_tool.ainvoke({})
            )
            assert owner_result["notes_markdown"] == "私人笔记"
            assert other_result["error"]["code"] == "notion_not_available"
            assert "私人笔记" not in json.dumps(other_result, ensure_ascii=False)
            assert demo_result["source"] == "demo"
            assert "私人笔记" not in demo_result["notes_markdown"]
            assert owner_session.state_read is True
            assert other_session.state_read is False
            assert other_session.citations == {}
            assert len(owner_session.executions) == len(other_session.executions) == 1
            service.notion.read.assert_awaited_once()
            assert await other_tool.ainvoke({}) == other_result
            assert len(owner_session.executions) == 1
            assert other_session.executions[-1].cached is True

    asyncio.run(run())
