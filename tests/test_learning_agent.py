import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, SecretStr

from ai_workspace.learning.agent import LearningAgent, _SecretGuard
from ai_workspace.learning.agent_errors import LearningAgentError
from ai_workspace.learning.config import LearningSettings
from ai_workspace.learning.demo import demo_learning_state
from ai_workspace.learning.documents import SECTIONS
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.models import OfficialDocument
from ai_workspace.learning.service import LearningService
from ai_workspace.providers import ModelMessage, ProviderError

pytestmark = pytest.mark.anyio
KEY = "fake-learning-model-secret"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class ScriptedModel(BaseChatModel):
    """执行真正 create_agent 循环，只替换网络模型的响应。"""

    responses: list[AIMessage]
    calls: int = 0
    seen: list[list[BaseMessage]] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    delay: float = 0
    fail: bool = False

    @property
    def _llm_type(self):
        return "scripted-learning-test"

    def bind_tools(self, tools, **kwargs):
        self.tool_names = [
            item["function"]["name"] if isinstance(item, dict) else item.name
            for item in tools
        ]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise AssertionError("Learning agents must use async calls")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ProviderError("upstream body " + KEY)
        response = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        return ChatResult(
            generations=[ChatGeneration(message=response.model_copy(deep=True))]
        )


def call(name, args, *, usage=True):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": uuid4().hex}],
        usage_metadata=(
            {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
            if usage
            else None
        ),
    )


def answer(*, ids=None, status="answered", text="异步测试示例来自所读取的官方章节。"):
    return call(
        "LearningAnswer",
        {
            "answer": text,
            "citation_ids": ["doc:async-tests"] if ids is None else ids,
            "status": status,
        },
    )


def source_service():
    service = AsyncMock(spec=LearningService)
    service.settings = LearningSettings(_env_file=None)
    service.read_state.return_value = demo_learning_state()
    text = "# Async Tests\n\nUse AsyncClient with ASGITransport."
    service.read_document.return_value = OfficialDocument(
        section_id="async-tests",
        title="Async Tests",
        url=SECTIONS[1].url,
        content_markdown=text,
        fetched_at=datetime.now(UTC),
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    return service


def setup_agent(responses, *, service=None, timeout=120, **model_options):
    model = ScriptedModel(responses=responses, **model_options)
    lifetime = {"opened": False, "closed": False}

    @asynccontextmanager
    async def factory(*, api_key, model: str) -> AsyncIterator[BaseChatModel]:
        assert api_key == KEY
        assert model == "test/model"
        lifetime["opened"] = True
        try:
            yield scripted
        finally:
            lifetime["closed"] = True

    scripted = model
    source = service or source_service()
    agent = LearningAgent(source, model_factory=factory, timeout_seconds=timeout)
    return agent, model, source, lifetime


async def run(agent, *, user_id=None, source="demo", messages=None):
    return await agent.run(
        user_id=user_id or uuid4(),
        api_key=KEY,
        model="test/model",
        messages=messages or [ModelMessage(role="user", content="继续学习异步测试")],
        source=source,
    )


def reads():
    return [
        call("get_learning_state", {}),
        call("read_official_doc", {"section_id": "async-tests"}),
    ]


async def test_real_agent_loop_reads_tools_and_returns_verified_sources():
    agent, model, service, lifetime = setup_agent([*reads(), answer()])
    response = await run(agent)
    assert response.source == "demo"
    assert response.content.startswith("演示模式：")
    assert SECTIONS[1].url in response.content
    assert [item.source_id for item in response.sources] == ["doc:async-tests"]
    assert [item.tool for item in response.tool_calls] == [
        "get_learning_state",
        "read_official_doc",
    ]
    assert model.calls == 3
    assert set(model.tool_names) == {
        "get_learning_state",
        "read_official_doc",
        "LearningAnswer",
    }
    assert any(isinstance(message, ToolMessage) for message in model.seen[-1])
    assert response.usage.total_tokens == 42
    service.read_document.assert_awaited_once_with("async-tests")
    assert lifetime == {"opened": True, "closed": True}


async def test_unretrieved_citation_is_rejected():
    agent, *_ = setup_agent([*reads(), answer(ids=["doc:testing"])])
    with pytest.raises(LearningAgentError, match="verified") as error:
        await run(agent)
    assert error.value.code == "learning_invalid_citation"


async def test_unsupported_answer_without_retrieval_is_rejected():
    agent, *_ = setup_agent([answer(ids=[])])
    with pytest.raises(LearningAgentError) as error:
        await run(agent)
    assert error.value.code == "learning_sources_required"


async def test_ambiguous_question_can_return_a_clarification_without_sources():
    agent, model, _, _ = setup_agent(
        [answer(ids=[], status="needs_clarification", text="你想学习哪一个测试章节？")]
    )
    response = await run(agent)
    assert response.status == "needs_clarification"
    assert response.sources == []
    assert response.tool_calls == []
    assert model.calls == 1


async def test_source_failure_is_visible_and_is_not_a_successful_citation():
    service = source_service()
    service.read_document.side_effect = LearningSourceError("source_timeout")
    agent, _, _, _ = setup_agent(
        [
            *reads(),
            answer(
                ids=[],
                status="needs_clarification",
                text="官方文档读取超时，请稍后重试。",
            ),
        ],
        service=service,
    )
    response = await run(agent)
    assert response.tool_calls[-1].status == "error"
    assert response.tool_calls[-1].error_code == "source_timeout"
    assert response.sources == []


async def test_missing_notion_configuration_fails_before_model_cost():
    agent, _, _, lifetime = setup_agent([answer()])
    with pytest.raises(LearningSourceError) as error:
        await run(agent, source="notion")
    assert error.value.code == "notion_not_configured"
    assert not lifetime["opened"]


async def test_notion_owner_cannot_be_changed_by_request():
    service = source_service()
    service.settings = LearningSettings(
        _env_file=None,
        notion_api_token=SecretStr("fake-notion-token"),
        notion_owner_user_id=uuid4(),
        notion_fastapi_page_id=uuid4(),
    )
    agent, _, _, lifetime = setup_agent([answer()], service=service)
    with pytest.raises(LearningSourceError) as error:
        await run(agent, source="notion")
    assert error.value.status_code == 404
    assert not lifetime["opened"]


async def test_model_call_limit_prevents_an_endless_loop():
    agent, model, service, lifetime = setup_agent([call("get_learning_state", {})])
    with pytest.raises(LearningAgentError) as error:
        await run(agent)
    assert error.value.code == "learning_agent_limit"
    assert 1 <= model.calls <= 5
    service.read_state.assert_awaited_once()
    assert lifetime["closed"]


async def test_timeout_closes_model_resources():
    agent, _, _, lifetime = setup_agent([answer()], timeout=0.03, delay=1)
    with pytest.raises(LearningAgentError) as error:
        await run(agent)
    assert error.value.code == "learning_agent_timeout"
    assert lifetime["closed"]


async def test_cancellation_is_propagated_and_closes_model_resources():
    agent, model, _, lifetime = setup_agent([answer()], delay=10)
    task = asyncio.create_task(run(agent))
    for _ in range(100):
        if model.calls:
            break
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lifetime["closed"]


async def test_provider_exception_is_sanitized():
    agent, _, _, lifetime = setup_agent([answer()], fail=True)
    with pytest.raises(LearningAgentError) as error:
        await run(agent)
    assert KEY not in str(error.value)
    assert lifetime["closed"]


async def test_known_secret_in_source_is_stopped_before_next_model_request():
    service = source_service()
    service.read_state.return_value = demo_learning_state().model_copy(
        update={"notes_markdown": "private note " + KEY}
    )
    agent, model, _, _ = setup_agent([*reads(), answer()], service=service)
    with pytest.raises(LearningAgentError):
        await run(agent)
    assert model.calls == 1


async def test_known_secret_in_model_output_is_rejected():
    agent, _, _, _ = setup_agent(
        [answer(ids=[], status="needs_clarification", text=KEY)]
    )
    with pytest.raises(LearningAgentError) as error:
        await run(agent)
    assert KEY not in str(error.value)


async def test_invalid_structured_output_has_controlled_error():
    agent, *_ = setup_agent([call("LearningAnswer", {"answer": ""})])
    with pytest.raises(LearningAgentError) as error:
        await run(agent)
    assert error.value.code == "learning_invalid_answer"


async def test_missing_usage_is_not_filled_with_zero():
    steps = reads()
    steps[0].usage_metadata = None
    agent, *_ = setup_agent([*steps, answer()])
    response = await run(agent)
    assert response.usage is None


async def test_machine_wide_langsmith_setting_does_not_enable_tracing(monkeypatch):
    from langsmith import Client

    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    def no_trace(*args, **kwargs):
        raise AssertionError("Private learning context must not leave via tracing")

    monkeypatch.setattr(Client, "create_run", no_trace)
    monkeypatch.setattr(Client, "update_run", no_trace)
    agent, *_ = setup_agent([*reads(), answer()])
    assert (await run(agent)).status == "answered"


@pytest.mark.parametrize("secret", ['fake-"-key', "fake-\\-key"])
async def test_secret_guard_checks_strings_before_json_escaping(secret):
    guard = _SecretGuard((secret,))
    with pytest.raises(LearningAgentError):
        guard._check([HumanMessage(content="note " + secret)])
    with pytest.raises(LearningAgentError):
        guard.check_payload({"answer": "note " + secret})
