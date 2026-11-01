"""A bounded MCP client for one configured, read-only Artificial Analysis service."""

import json
import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Self

import anyio
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_workspace.agents.aa_config import AASettings

AA_SOURCE_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
AA_VALUE_FORMULA = "intelligence_index / evaluation_task_cost"
MAX_RESULT_BYTES = 256 * 1024
TOOL_NAMES = frozenset({"query_model_rankings", "compare_models"})
type ToolName = Literal["query_model_rankings", "compare_models"]
type ModelId = Annotated[str, Field(min_length=1, max_length=200)]
type SnapshotId = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
type Count = Annotated[int, Field(ge=0, le=50_000)]
type Finite = Annotated[float, Field(allow_inf_nan=False)]
type Issue = Literal[
    "invalid_record",
    "missing_or_invalid_I",
    "missing_or_invalid_C",
    "non_positive_I_or_C",
    "invalid_identity",
]


class AARankingQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    metric: Literal["value", "intelligence", "task_cost"] = "value"
    query: str = Field(default="", max_length=100)
    min_intelligence: Finite | None = Field(default=None, ge=0)
    max_task_cost: Finite | None = Field(default=None, gt=0)
    pareto_only: bool = False
    limit: int = Field(default=10, ge=1, le=50)


class AAComparisonQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_ids: list[ModelId] = Field(min_length=1, max_length=10)
    expected_snapshot_id: SnapshotId | None = None


class _ResponseModel(BaseModel):
    # Forward only the explicit contract, never arbitrary upstream keys.
    model_config = ConfigDict(extra="ignore", strict=True)


class AAMetadata(_ResponseModel):
    source: Literal["Artificial Analysis"]
    source_url: Literal[AA_SOURCE_URL]
    snapshot_id: SnapshotId
    fetched_at_utc: str | None = Field(max_length=50)
    index_version: int = Field(ge=1)
    tier: str | None = Field(default=None, max_length=100)
    total_configurations: Count
    eligible_configurations: Count
    excluded_reasons: dict[Issue, Count]
    value_formula: Literal[AA_VALUE_FORMULA]
    cost_definition: str = Field(min_length=1, max_length=500)
    ranking_scope: str = Field(min_length=1, max_length=300)
    warnings: list[Annotated[str, Field(max_length=500)]] = Field(max_length=10)

    @model_validator(mode="after")
    def validate_metadata(self) -> Self:
        if self.eligible_configurations > self.total_configurations:
            raise ValueError("Invalid sample counts")
        if sum(self.excluded_reasons.values()) != (
            self.total_configurations - self.eligible_configurations
        ):
            raise ValueError("Inconsistent sample counts")
        if self.fetched_at_utc is not None:
            parsed = datetime.fromisoformat(self.fetched_at_utc)
            if parsed.tzinfo is None or parsed > datetime.now(UTC) + timedelta(
                minutes=5
            ):
                raise ValueError("Invalid collection time")
            self.fetched_at_utc = parsed.astimezone(UTC).isoformat()
        return self


class AAModelItem(_ResponseModel):
    id: ModelId
    name: str = Field(min_length=1, max_length=300)
    creator: str | None = Field(max_length=200)
    release_date: str | None = Field(max_length=40)
    intelligence_index: Finite | None
    evaluation_task_cost: Finite | None
    value_score: Finite | None
    ranking_eligible: bool
    missing_metrics: list[Literal["intelligence_index", "evaluation_task_cost"]] = (
        Field(max_length=2)
    )

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        expected_missing = [
            name
            for name in ("intelligence_index", "evaluation_task_cost")
            if getattr(self, name) is None
        ]
        if self.missing_metrics != expected_missing:
            raise ValueError("Inconsistent missing metrics")
        positive = (
            self.intelligence_index is not None
            and self.intelligence_index > 0
            and self.evaluation_task_cost is not None
            and self.evaluation_task_cost > 0
        )
        if self.ranking_eligible != positive:
            raise ValueError("Inconsistent eligibility")
        if positive and (self.value_score is None or self.value_score <= 0):
            raise ValueError("Missing value score")
        if not positive and self.value_score is not None:
            raise ValueError("Unexpected value score")
        return self


class _RankedItem(AAModelItem):
    rank: int = Field(ge=1, le=50)


class _RankingResponse(_ResponseModel):
    metadata: AAMetadata
    filters: AARankingQuery
    filtered_count: Count
    rank_scope: str = Field(min_length=1, max_length=300)
    pareto_scope: str = Field(min_length=1, max_length=300)
    items: list[_RankedItem] = Field(max_length=50)

    @model_validator(mode="after")
    def validate_results(self) -> Self:
        if (
            not len(self.items)
            <= self.filtered_count
            <= self.metadata.eligible_configurations
        ):
            raise ValueError("Invalid result count")
        if [item.rank for item in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("Invalid ranks")
        if any(not item.ranking_eligible for item in self.items):
            raise ValueError("Ineligible ranked configuration")
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate configuration")
        return self


class _ComparisonResponse(_ResponseModel):
    metadata: AAMetadata
    items: list[AAModelItem] = Field(max_length=10)
    unknown_model_ids: list[ModelId] = Field(max_length=10)

    @model_validator(mode="after")
    def validate_results(self) -> Self:
        ids = [item.id for item in self.items] + self.unknown_model_ids
        if len(set(ids)) != len(ids) or len(ids) > 10:
            raise ValueError("Inconsistent comparison IDs")
        return self


class AAClientError(Exception):
    def __init__(self, code: str = "unavailable") -> None:
        self.code = code
        messages = {
            "unavailable": "AA 模型数据工具暂时不可用，请稍后重试。",
            "invalid_result": "AA 工具返回的数据未通过校验，本轮不能引用。",
            "invalid_arguments": "AA 查询参数无效，请检查筛选条件。",
            "snapshot_changed": "AA 快照已变化，请重新查询后再比较。",
        }
        super().__init__(messages.get(code, messages["unavailable"]))


@asynccontextmanager
async def _open_client(parameters: StdioServerParameters):
    # Never inherit service credentials or expose server stderr to application logs.
    with open(os.devnull, "w", encoding="utf-8") as errlog:  # noqa: ASYNC230 - null device only
        async with Client(
            stdio_client(parameters, errlog=errlog), cache=None
        ) as client:
            yield client


class AAClient:
    def __init__(
        self,
        settings: AASettings,
        *,
        client_factory: Callable[
            [StdioServerParameters], AbstractAsyncContextManager[Any]
        ] = _open_client,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory

    async def call(self, name: ToolName, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in TOOL_NAMES:
            raise AAClientError("invalid_arguments")
        input_model = (
            AARankingQuery if name == "query_model_rankings" else AAComparisonQuery
        )
        try:
            options = input_model.model_validate(arguments)
        except ValidationError:
            raise AAClientError("invalid_arguments") from None
        if not self.settings.enabled:
            raise AAClientError()
        parameters = StdioServerParameters(
            command=str(self.settings.python_executable),
            args=[
                "-m",
                "aa_analysis.server",
                "--snapshot",
                str(self.settings.snapshot_path),
            ],
            # SDK inherits only its platform allowlist; {} never merges os.environ.
            env={},
            cwd=str(self.settings.snapshot_path.parent),
        )
        try:
            with anyio.fail_after(self.settings.timeout_seconds):
                async with self._client_factory(parameters) as client:
                    listing = await client.list_tools()
                    tools = {tool.name: tool for tool in listing.tools}
                    selected = tools.get(name)
                    if (
                        len(listing.tools) > 20
                        or selected is None
                        or selected.annotations is None
                        or selected.annotations.read_only_hint is not True
                        or selected.annotations.open_world_hint is not False
                    ):
                        raise AAClientError("invalid_result")
                    result = await client.call_tool(name, options.model_dump())
                    if result.is_error:
                        # Recognize only the exact expected service error; discard all other text.
                        changed = "Snapshot changed. Query rankings again before comparing models."
                        if any(
                            getattr(item, "text", None) == changed
                            for item in result.content
                        ):
                            raise AAClientError("snapshot_changed")
                        raise AAClientError()
                    payload = result.structured_content
                    return _validate_result(name, payload, options)
        except AAClientError:
            raise
        except Exception:  # noqa: BLE001 - never expose SDK or subprocess exception bodies
            # asyncio cancellation is BaseException and must propagate to the caller.
            raise AAClientError() from None


def _validate_result(
    name: ToolName, payload: Any, options: BaseModel
) -> dict[str, Any]:
    try:
        if len(json.dumps(payload, allow_nan=False).encode("utf-8")) > MAX_RESULT_BYTES:
            raise ValueError("Oversized response")
        if name == "query_model_rankings":
            parsed = _RankingResponse.model_validate(payload)
            if parsed.filters != options or len(parsed.items) > options.limit:
                raise ValueError("Unexpected query response")
        else:
            parsed = _ComparisonResponse.model_validate(payload)
            actual_ids = {item.id for item in parsed.items} | set(
                parsed.unknown_model_ids
            )
            if actual_ids != set(options.model_ids):
                raise ValueError("Unexpected comparison response")
            if (
                options.expected_snapshot_id is not None
                and parsed.metadata.snapshot_id != options.expected_snapshot_id
            ):
                raise AAClientError("snapshot_changed")
        return parsed.model_dump(mode="json")
    except AAClientError:
        raise
    except ValueError, TypeError, OverflowError, RecursionError:
        raise AAClientError("invalid_result") from None
