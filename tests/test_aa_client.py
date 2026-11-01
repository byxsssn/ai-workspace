import asyncio
import copy
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ai_workspace.agents.aa_client import (
    AA_SOURCE_URL,
    AA_VALUE_FORMULA,
    AAClient,
    AAClientError,
    AARankingQuery,
)
from ai_workspace.agents.aa_config import AASettings


@pytest.fixture
def anyio_backend():
    return "asyncio"


def settings(tmp_path, **changes):
    return AASettings(
        _env_file=None,
        **{
            "enabled": True,
            "python_executable": Path(sys.executable),
            "snapshot_path": tmp_path / "snapshot.json",
            **changes,
        },
    )


def metadata():
    return {
        "source": "Artificial Analysis",
        "source_url": AA_SOURCE_URL,
        "snapshot_id": "a" * 64,
        "fetched_at_utc": None,
        "index_version": 5,
        "tier": "free",
        "total_configurations": 2,
        "eligible_configurations": 1,
        "excluded_reasons": {"missing_or_invalid_I": 1},
        "value_formula": AA_VALUE_FORMULA,
        "cost_definition": "AA evaluation task cost",
        "ranking_scope": "Common eligible sample",
        "warnings": ["Collection time unknown"],
    }


def model_item():
    return {
        "id": "model-id",
        "name": "Example",
        "creator": "Example creator",
        "release_date": None,
        "intelligence_index": 40.0,
        "evaluation_task_cost": 2.0,
        "value_score": 20.0,
        "ranking_eligible": True,
        "missing_metrics": [],
    }


def ranking():
    return {
        "metadata": metadata(),
        "filters": AARankingQuery().model_dump(),
        "filtered_count": 1,
        "rank_scope": "Filtered",
        "pareto_scope": "All eligible",
        "items": [{"rank": 1, **model_item()}],
    }


class FakeClient:
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else ranking()
        self.opened = self.closed = False
        self.parameters = None
        self.called = []
        self.error = None
        self.result_error = None
        self.delay = 0
        self.read_only = True

    @asynccontextmanager
    async def factory(self, parameters):
        self.parameters = parameters
        self.opened = True
        try:
            yield self
        finally:
            self.closed = True

    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name=name,
                    annotations=SimpleNamespace(
                        read_only_hint=self.read_only,
                        open_world_hint=False,
                    ),
                )
                for name in ("query_model_rankings", "compare_models")
            ]
        )

    async def call_tool(self, name, arguments):
        self.called.append((name, arguments))
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return SimpleNamespace(
            is_error=self.result_error is not None,
            content=[SimpleNamespace(text=self.result_error)]
            if self.result_error
            else [],
            structured_content=self.payload,
        )


def test_settings_are_disabled_by_default():
    assert AASettings(_env_file=None).enabled is False


@pytest.mark.parametrize(
    "changes",
    [
        {"python_executable": None},
        {"snapshot_path": None},
        {"python_executable": Path("python")},
        {"snapshot_path": Path("relative.json")},
        {"timeout_seconds": 0},
        {"timeout_seconds": 31},
    ],
)
def test_enabled_configuration_requires_absolute_paths(tmp_path, changes):
    with pytest.raises(ValidationError):
        settings(tmp_path, **changes)


def test_configuration_does_not_require_running_service_or_existing_files(tmp_path):
    configured = settings(tmp_path, python_executable=tmp_path / "not-installed-python")
    assert configured.enabled is True


@pytest.mark.anyio
async def test_valid_query_uses_fixed_command_and_closes_client(tmp_path, monkeypatch):
    monkeypatch.setenv("APIKEY", "DO-NOT-INHERIT")
    fake = FakeClient()
    configured = settings(tmp_path)
    result = await AAClient(configured, client_factory=fake.factory).call(
        "query_model_rankings", {}
    )
    assert result["items"][0]["value_score"] == 20
    assert fake.opened and fake.closed
    assert fake.parameters.command == sys.executable
    assert fake.parameters.args == [
        "-m",
        "aa_analysis.server",
        "--snapshot",
        str(configured.snapshot_path),
    ]
    assert fake.parameters.env == {}
    assert fake.parameters.cwd == str(tmp_path)
    assert "DO-NOT-INHERIT" not in json.dumps(result)


@pytest.mark.anyio
async def test_unknown_fields_are_not_forwarded(tmp_path):
    payload = ranking()
    payload["internal_path"] = "private-path"
    payload["metadata"]["api_key"] = "secret-sentinel"
    payload["items"][0]["unexpected"] = "secret-sentinel"
    fake = FakeClient(payload)
    result = await AAClient(settings(tmp_path), client_factory=fake.factory).call(
        "query_model_rankings", {}
    )
    assert "secret-sentinel" not in json.dumps(result)
    assert "private-path" not in json.dumps(result)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        {"limit": 0},
        {"limit": True},
        {"snapshot_path": ".env"},
        {"metric": "tokens"},
        {"max_task_cost": float("inf")},
    ],
)
async def test_invalid_query_does_not_start_process(tmp_path, arguments):
    fake = FakeClient()
    with pytest.raises(AAClientError) as error:
        await AAClient(settings(tmp_path), client_factory=fake.factory).call(
            "query_model_rankings", arguments
        )
    assert error.value.code == "invalid_arguments"
    assert not fake.opened


@pytest.mark.anyio
async def test_disabled_or_unknown_tool_does_not_start_process(tmp_path):
    fake = FakeClient()
    client = AAClient(settings(tmp_path, enabled=False), client_factory=fake.factory)
    with pytest.raises(AAClientError):
        await client.call("query_model_rankings", {})
    with pytest.raises(AAClientError):
        await client.call("refresh_snapshot", {})
    assert not fake.opened


@pytest.mark.anyio
async def test_refuses_tool_not_declared_read_only(tmp_path):
    fake = FakeClient()
    fake.read_only = False
    with pytest.raises(AAClientError) as error:
        await AAClient(settings(tmp_path), client_factory=fake.factory).call(
            "query_model_rankings", {}
        )
    assert error.value.code == "invalid_result"
    assert fake.closed and not fake.called


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        lambda x: x["metadata"].update(source_url="https://untrusted.test"),
        lambda x: x["metadata"].update(snapshot_id="invalid"),
        lambda x: x["metadata"].update(index_version=True),
        lambda x: x["metadata"].update(eligible_configurations=3),
        lambda x: x["metadata"].update(fetched_at_utc="2026-01-01"),
        lambda x: x["metadata"].update(excluded_reasons={}),
        lambda x: x["items"][0].update(value_score=float("nan")),
        lambda x: x["items"][0].update(rank=0),
        lambda x: x["items"][0].update(name="x" * 301),
        lambda x: x.update(filtered_count=0),
        lambda x: x.update(filters={"limit": 1}),
        lambda x: x.update(secret_padding="x" * (256 * 1024)),
    ],
)
async def test_untrusted_response_is_bounded_and_checked(tmp_path, change):
    payload = ranking()
    change(payload)
    fake = FakeClient(payload)
    with pytest.raises(AAClientError) as error:
        await AAClient(settings(tmp_path), client_factory=fake.factory).call(
            "query_model_rankings", {}
        )
    assert error.value.code == "invalid_result"
    assert fake.closed


@pytest.mark.anyio
async def test_comparison_and_snapshot_validation(tmp_path):
    payload = {
        "metadata": metadata(),
        "items": [model_item()],
        "unknown_model_ids": ["x"],
    }
    fake = FakeClient(payload)
    client = AAClient(settings(tmp_path), client_factory=fake.factory)
    result = await client.call(
        "compare_models",
        {
            "model_ids": ["model-id", "x"],
            "expected_snapshot_id": "a" * 64,
        },
    )
    assert result["unknown_model_ids"] == ["x"]
    with pytest.raises(AAClientError) as error:
        await client.call(
            "compare_models",
            {
                "model_ids": ["model-id", "x"],
                "expected_snapshot_id": "b" * 64,
            },
        )
    assert error.value.code == "snapshot_changed"
    with pytest.raises(AAClientError) as error:
        await client.call("compare_models", {"model_ids": ["wrong-id"]})
    assert error.value.code == "invalid_result"


@pytest.mark.anyio
async def test_sdk_exceptions_and_tool_error_text_are_sanitized(tmp_path):
    fake = FakeClient()
    fake.error = ValueError("secret-api-key and private filesystem path")
    client = AAClient(settings(tmp_path), client_factory=fake.factory)
    with pytest.raises(AAClientError) as error:
        await client.call("query_model_rankings", {})
    assert "secret-api-key" not in str(error.value)
    assert fake.closed
    fake.error = None
    fake.result_error = "upstream-private-secret"
    with pytest.raises(AAClientError) as error:
        await client.call("query_model_rankings", {})
    assert "upstream-private-secret" not in str(error.value)
    fake.result_error = (
        "Snapshot changed. Query rankings again before comparing models."
    )
    with pytest.raises(AAClientError) as error:
        await client.call("query_model_rankings", {})
    assert error.value.code == "snapshot_changed"


@pytest.mark.anyio
async def test_timeout_and_cancellation_close_client(tmp_path):
    fake = FakeClient()
    fake.delay = 30
    configured = settings(tmp_path).model_copy(update={"timeout_seconds": 0.01})
    client = AAClient(configured, client_factory=fake.factory)
    with pytest.raises(AAClientError):
        await client.call("query_model_rankings", {})
    assert fake.closed
    fake.closed = False
    client = AAClient(settings(tmp_path), client_factory=fake.factory)
    task = asyncio.create_task(client.call("query_model_rankings", {}))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.closed


@pytest.mark.anyio
async def test_real_stdio_client_calls_fixed_module_without_secret_env(
    tmp_path, monkeypatch
):
    package = tmp_path / "aa_analysis"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    payload = copy.deepcopy(ranking())
    script = (
        "import os\n"
        "from typing import Any\n"
        "from mcp.server import MCPServer\n"
        "from mcp.types import ToolAnnotations\n"
        f"PAYLOAD = {payload!r}\n"
        "server = MCPServer('fixture')\n"
        "@server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))\n"
        "def query_model_rankings(metric: str='value', query: str='', "
        "min_intelligence: float|None=None, max_task_cost: float|None=None, "
        "pareto_only: bool=False, limit: int=10) -> dict[str, Any]:\n"
        "    assert 'APIKEY' not in os.environ\n"
        "    assert 'AI_WORKSPACE_JWT_SECRET_KEY' not in os.environ\n"
        "    return PAYLOAD\n"
        "server.run(transport='stdio')\n"
    )
    (package / "server.py").write_text(script, encoding="utf-8")
    monkeypatch.setenv("APIKEY", "sentinel-aa-key")
    monkeypatch.setenv("AI_WORKSPACE_JWT_SECRET_KEY", "sentinel-jwt-key")
    result = await AAClient(settings(tmp_path)).call("query_model_rankings", {})
    assert result["items"][0]["id"] == "model-id"
    assert "sentinel" not in json.dumps(result)
