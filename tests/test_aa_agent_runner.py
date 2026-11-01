"""Run the real unified Agent loop against deterministic AA MCP responses."""

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from test_agent_runner import (
    KEY,
    ScriptedModel,
    answer,
    call,
    ranking_service,
    reads,
    run,
    source_service,
)

from ai_workspace.agents.runner import AgentRunner
from ai_workspace.providers import ProviderError

pytestmark = pytest.mark.anyio
AA_SOURCE = "https://artificialanalysis.ai/api/v2/language/models/free"
AA_HASH = "b" * 64


@pytest.fixture
def anyio_backend():
    return "asyncio"


def aa_result(*, compare=False, snapshot_id=AA_HASH):
    result = {
        "metadata": {
            "source": "Artificial Analysis",
            "source_url": AA_SOURCE,
            "snapshot_id": snapshot_id,
            "fetched_at_utc": None,
            "index_version": 5,
            "tier": "free",
            "total_configurations": 3,
            "eligible_configurations": 2,
            "excluded_reasons": {"missing_or_invalid_I": 1},
            "value_formula": "intelligence_index / evaluation_task_cost",
            "cost_definition": (
                "AA evaluation task cost, not API price per token or workload cost."
            ),
            "ranking_scope": "Configurations with positive, finite I and C.",
            "warnings": ["Collection time is unknown for this legacy snapshot."],
        },
        "items": [
            {
                "id": model_id,
                "name": name,
                "creator": "Example",
                "release_date": "2026-01-01",
                "intelligence_index": intelligence,
                "evaluation_task_cost": cost,
                "value_score": intelligence / cost,
                "ranking_eligible": True,
                "missing_metrics": [],
                **({} if compare else {"rank": rank}),
            }
            for rank, (model_id, name, intelligence, cost) in enumerate(
                (("alpha", "Alpha", 40.0, 2.0), ("beta", "Beta", 60.0, 6.0)), 1
            )
        ],
    }
    if compare:
        result["unknown_model_ids"] = []
    else:
        result.update(
            filters={
                "metric": "value",
                "query": "",
                "min_intelligence": None,
                "max_task_cost": None,
                "pareto_only": False,
                "limit": 10,
            },
            filtered_count=2,
            rank_scope="Filtered results.",
            pareto_scope="All eligible configurations.",
        )
    return result


def aa_client():
    async def respond(name, arguments):
        return aa_result(compare=name == "compare_models")

    return SimpleNamespace(call=AsyncMock(side_effect=respond))


def setup_agent(responses, *, client=None, rankings=None, timeout=120):
    model = ScriptedModel(responses=responses)
    learning = source_service()
    lifetime = {"opened": False, "closed": False}

    @asynccontextmanager
    async def factory(*, api_key, **options):
        assert api_key == KEY
        lifetime["opened"] = True
        try:
            yield model
        finally:
            lifetime["closed"] = True

    runner = AgentRunner(
        learning,
        aa_client=client,
        rankings=rankings,
        model_factory=factory,
        timeout_seconds=timeout,
    )
    return runner, model, learning, lifetime


def tool_results(model):
    return [
        json.loads(message.content)
        for message in model.seen[-1]
        if isinstance(message, ToolMessage)
    ]


async def test_aa_tools_are_lazy_and_do_not_change_greeting_or_read_private_records():
    client, rankings = aa_client(), ranking_service()
    agent, model, learning, lifetime = setup_agent(
        [AIMessage(content="你好！")], client=client, rankings=rankings
    )
    result = await run(agent, source=None)
    assert result.content == "你好！"
    assert set(model.tool_names) == {
        "query_pypi_rankings",
        "query_model_rankings",
        "compare_models",
    }
    client.call.assert_not_awaited()
    rankings.list_rankings.assert_not_awaited()
    learning.read_state.assert_not_called()
    learning.read_document.assert_not_called()
    assert lifetime == {"opened": True, "closed": True}


async def test_unconfigured_aa_does_not_advertise_tools_or_add_citations():
    agent, model, _, _ = setup_agent([AIMessage(content="普通对话。")])
    result = await run(agent, source=None)
    assert model.tool_names == []
    assert result.content == "普通对话。"


async def test_query_then_compare_pins_snapshot_and_saves_actual_source():
    client = aa_client()
    agent, model, learning, lifetime = setup_agent(
        [
            call("query_model_rankings", {"limit": 2}),
            call("compare_models", {"model_ids": ["alpha", "beta"]}),
            AIMessage(
                content="Alpha 的评测任务成本更低，Beta 的能力指数更高。",
                response_metadata={"model_name": "resolved/aa-model"},
                usage_metadata={
                    "input_tokens": 20,
                    "output_tokens": 5,
                    "total_tokens": 25,
                },
            ),
        ],
        client=client,
    )
    result = await run(agent, source=None)
    assert result.model == "resolved/aa-model"
    assert result.usage.total_tokens == 53
    assert result.content.startswith("Alpha")
    assert "Artificial Analysis" in result.content
    assert AA_SOURCE in result.content
    assert AA_HASH[:12] in result.content
    assert "未知" in result.content
    assert "评测" in result.content
    assert client.call.await_count == 2
    assert client.call.await_args_list[0].args[0] == "query_model_rankings"
    compare_name, compare_args = client.call.await_args_list[1].args
    assert compare_name == "compare_models"
    assert compare_args["expected_snapshot_id"] == AA_HASH
    assert compare_args["model_ids"] == ["alpha", "beta"]
    retrieved = tool_results(model)
    assert len(retrieved) == 2
    assert all(item["metadata"]["snapshot_id"] == AA_HASH for item in retrieved)
    learning.read_state.assert_not_called()
    assert lifetime["closed"]


async def test_learning_mode_does_not_receive_or_call_aa_tools():
    client = aa_client()
    agent, model, _, _ = setup_agent([*reads(), answer()], client=client)
    result = await run(agent, source="demo")
    assert result.status == "answered"
    assert "query_model_rankings" not in model.tool_names
    assert "compare_models" not in model.tool_names
    client.call.assert_not_awaited()


async def test_compare_without_discovery_never_calls_mcp_or_preserves_fabricated_numbers():
    client = aa_client()
    agent, _, _, _ = setup_agent(
        [
            call("compare_models", {"model_ids": ["guessed-model"]}),
            AIMessage(content="guessed-model 排名第一，评分 999999999。"),
        ],
        client=client,
    )
    result = await run(agent, source=None)
    client.call.assert_not_awaited()
    assert "999999999" not in result.content
    assert "排名第一" not in result.content
    assert AA_SOURCE not in result.content


@pytest.mark.parametrize(
    "arguments",
    [
        {"limit": 0},
        {"limit": 51},
        {"limit": True},
        {"metric": "token_price"},
        {"snapshot_path": "../.env"},
        {"url": "https://untrusted.invalid"},
        {"min_intelligence": -1},
    ],
)
async def test_invalid_query_arguments_never_reach_mcp(arguments):
    client = aa_client()
    agent, _, _, _ = setup_agent(
        [
            call("query_model_rankings", arguments),
            AIMessage(content="评分 999999999。"),
        ],
        client=client,
    )
    result = await run(agent, source=None)
    client.call.assert_not_awaited()
    assert "999999999" not in result.content
    assert AA_SOURCE not in result.content


async def test_unavailable_mcp_has_no_secret_or_false_citation_in_saved_answer():
    client = aa_client()
    client.call.side_effect = RuntimeError("private subprocess path " + KEY)
    agent, model, _, lifetime = setup_agent(
        [
            call("query_model_rankings", {}),
            AIMessage(content="Alpha 排名第一，评分 999999999。"),
        ],
        client=client,
    )
    result = await run(agent, source=None)
    assert "999999999" not in result.content
    assert "排名第一" not in result.content
    assert AA_SOURCE not in result.content
    assert KEY not in str(model.seen)
    assert "private subprocess" not in str(model.seen)
    assert lifetime["closed"]


async def test_successful_aa_and_pypi_sources_are_both_retained():
    client, rankings = aa_client(), ranking_service()
    agent, model, _, _ = setup_agent(
        [
            call("query_pypi_rankings", {"projects": ["fastapi"]}),
            call("query_model_rankings", {}),
            call("compare_models", {"model_ids": ["alpha", "beta"]}),
            AIMessage(content="按两份数据分别比较框架下载量与模型评测成本。"),
        ],
        client=client,
        rankings=rankings,
    )
    result = await run(agent, source=None)
    assert result.content.startswith("按两份数据")
    assert "统计月份：2026-09" in result.content
    assert rankings.list_rankings.return_value.source_url in result.content
    assert AA_SOURCE in result.content
    assert "a" * 12 in result.content and AA_HASH[:12] in result.content
    assert model.calls == 4


@pytest.mark.parametrize("failed_source", ["aa", "pypi"])
async def test_mixed_source_failure_discards_invented_text_but_keeps_successful_source(
    failed_source,
):
    client, rankings = aa_client(), ranking_service()
    if failed_source == "aa":
        client.call.side_effect = RuntimeError("AA unavailable")
    else:
        rankings.list_rankings.side_effect = RuntimeError("PyPI unavailable")
    agent, _, _, _ = setup_agent(
        [
            call("query_pypi_rankings", {}),
            call("query_model_rankings", {}),
            AIMessage(content="两份最新排名都是第一，数字 999999999。"),
        ],
        client=client,
        rankings=rankings,
    )
    result = await run(agent, source=None)
    assert "999999999" not in result.content
    assert "都是第一" not in result.content
    successful_url = (
        rankings.list_rankings.return_value.source_url
        if failed_source == "aa"
        else AA_SOURCE
    )
    failed_url = (
        AA_SOURCE
        if failed_source == "aa"
        else ("https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json")
    )
    assert successful_url in result.content
    assert failed_url not in result.content


async def test_changed_aa_snapshot_is_not_given_to_model_as_a_second_success():
    client = aa_client()
    client.call.side_effect = [aa_result(), aa_result(snapshot_id="c" * 64)]
    agent, model, _, _ = setup_agent(
        [
            call("query_model_rankings", {"limit": 2}),
            call("query_model_rankings", {"metric": "intelligence"}),
            AIMessage(content="跨快照得出增长率 999999999。"),
        ],
        client=client,
    )
    result = await run(agent, source=None)
    assert "999999999" not in result.content
    assert "c" * 64 not in str(model.seen)
    assert AA_HASH[:12] in result.content


async def test_known_secret_in_aa_result_stops_before_next_model_request():
    client = aa_client()
    response = aa_result()
    response["items"][0]["name"] = KEY
    client.call.side_effect = None
    client.call.return_value = response
    agent, model, _, lifetime = setup_agent(
        [call("query_model_rankings", {}), AIMessage(content="Should not reach model")],
        client=client,
    )
    with pytest.raises(ProviderError) as error:
        await run(agent, source=None)
    assert KEY not in str(error.value)
    assert model.calls == 1
    assert lifetime["closed"]


async def test_secret_in_tool_arguments_is_stopped_before_mcp_call():
    client = aa_client()
    agent, _, _, _ = setup_agent(
        [call("query_model_rankings", {"query": KEY})], client=client
    )
    with pytest.raises(ProviderError):
        await run(agent, source=None)
    client.call.assert_not_awaited()


async def test_aa_call_cancellation_propagates_and_closes_model_context():
    client = aa_client()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def wait_for_cancel(name, arguments):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    client.call.side_effect = wait_for_cancel
    agent, _, _, lifetime = setup_agent(
        [call("query_model_rankings", {})], client=client
    )
    task = asyncio.create_task(run(agent, source=None))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert lifetime["closed"]


async def test_aa_total_timeout_cancels_tool_and_preserves_safe_error():
    client = aa_client()
    cancelled = asyncio.Event()

    async def wait_for_timeout(name, arguments):
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    client.call.side_effect = wait_for_timeout
    agent, _, _, lifetime = setup_agent(
        [call("query_model_rankings", {})], client=client, timeout=0.05
    )
    with pytest.raises(ProviderError, match="timed out"):
        await run(agent, source=None)
    assert cancelled.is_set()
    assert lifetime["closed"]


async def test_aa_enabled_model_budget_stops_repeated_tool_requests():
    client = aa_client()
    agent, model, _, lifetime = setup_agent(
        [call("query_model_rankings", {})], client=client
    )
    with pytest.raises(ProviderError, match="execution limit"):
        await run(agent, source=None)
    assert model.calls <= 5
    assert client.call.await_count <= 4
    assert lifetime["closed"]


async def test_aa_result_is_not_mutated_when_snapshot_pin_is_added():
    client = aa_client()
    source = aa_result()
    original = copy.deepcopy(source)
    client.call.side_effect = [source, aa_result(compare=True)]
    agent, _, _, _ = setup_agent(
        [
            call("query_model_rankings", {}),
            call("compare_models", {"model_ids": ["alpha"]}),
            AIMessage(content="按同一份快照比较。"),
        ],
        client=client,
    )
    await run(agent, source=None)
    assert source == original


@pytest.mark.parametrize("first_turn_fails", [False, True])
async def test_turns_do_not_share_failure_cache_or_snapshot_pin(first_turn_fails):
    client = aa_client()
    new_hash = "d" * 64
    first_results = (
        [RuntimeError("unavailable during first turn")]
        if first_turn_fails
        else [aa_result(), aa_result()]
    )
    client.call.side_effect = [
        *first_results,
        aa_result(snapshot_id=new_hash),
        aa_result(compare=True, snapshot_id=new_hash),
    ]
    agent, model, _, _ = setup_agent(
        [
            call("query_model_rankings", {}),
            call("query_model_rankings", {"query": "Alpha"}),
            AIMessage(content="第一轮结果。"),
        ],
        client=client,
    )
    first_response = await run(agent, source=None)
    assert client.call.await_count == len(first_results)
    if first_turn_fails:
        assert AA_SOURCE not in first_response.content
    else:
        assert AA_HASH[:12] in first_response.content

    # The same application-level runner/client can serve a later conversation turn.
    model.calls = 0
    model.seen = []
    model.responses = [
        call("query_model_rankings", {}),
        call("compare_models", {"model_ids": ["alpha", "beta"]}),
        AIMessage(content="第二轮使用新的快照。"),
    ]
    second_response = await run(agent, source=None)
    assert second_response.content.startswith("第二轮使用新的快照。")
    assert new_hash[:12] in second_response.content
    assert AA_HASH[:12] not in second_response.content
    assert client.call.await_count == len(first_results) + 2
    assert client.call.await_args.args[1]["expected_snapshot_id"] == new_hash
