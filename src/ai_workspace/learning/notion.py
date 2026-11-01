import asyncio
import json
import re
from datetime import UTC, datetime
from uuid import UUID

import httpx2

from ai_workspace.learning.catalog_models import (
    LearningRecordDetail,
    LearningRecordList,
    LearningRecordSummary,
)
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.http import fetch_source_bytes
from ai_workspace.learning.models import LearningState

# 只在这里耦合个人学习表的字段名称；Agent 不需要理解 Notion 属性对象。
NOTION_FIELD_MAP = {
    "topic_name": ("技术 / 主题", "title"),
    "learning_status": ("学习状态", "select"),
    "current_progress": ("当前进度", "rich_text"),
    "next_step": ("下一步", "rich_text"),
    "proficiency": ("掌握程度", "select"),
    "project_practice": ("项目实践", "checkbox"),
    "official_docs_url": ("官方文档", "url"),
    "evidence_url": ("成果链接", "url"),
}
_RECORD_FIELD_MAP = {
    **NOTION_FIELD_MAP,
    "category": ("类型", "select"),
    "priority": ("求职优先级", "select"),
}
_NOTION_API = "https://api.notion.com/v1/pages"
_NOTION_QUERY_API = "https://api.notion.com/v1/data_sources"
_NOTION_VERSION = "2026-03-11"
_UNKNOWN_BLOCK = re.compile(r"<unknown(?:\s|/?>)", re.IGNORECASE)
_MAX_RECORDS = 200
_QUERY_PAGE_SIZE = 100
_MAX_QUERY_PAGES = 10
_MAX_QUERY_BYTES = 3_000_000
_CATALOG_TIMEOUT_SECONDS = 30


class NotionLearningReader:
    """读取一个已授权的学习记录，不修改页面或共享客户端的凭据。"""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def read(self, *, token: str, page_id: UUID) -> LearningState:
        headers = _headers(token)
        url = f"{_NOTION_API}/{page_id}"
        page = _load_object(
            await fetch_source_bytes(
                self.client, url, headers=headers, sensitive_values=(token,)
            ),
            token,
        )
        _validate_identity(page, "page", page_id)
        _validate_active(page)
        values, warnings, updated_at = _page_properties(page, NOTION_FIELD_MAP)

        content = _load_object(
            await fetch_source_bytes(
                self.client,
                f"{url}/markdown",
                headers=headers,
                sensitive_values=(token,),
            ),
            token,
        )
        _validate_identity(content, "page_markdown", page_id)
        markdown, notes_complete, content_warnings = _notes(content)
        return LearningState(
            **values,
            source="notion",
            # 使用已核验的 ID 构造链接，不信任上游任意 URL。
            source_url=f"https://www.notion.so/{page_id.hex}",
            source_updated_at=updated_at,
            fetched_at=datetime.now(UTC),
            notes_markdown=markdown,
            notes_complete=notes_complete,
            warnings=[*warnings, *content_warnings],
        )

    async def list_records(
        self,
        *,
        token: str,
        data_source_id: UUID,
        fastapi_page_id: UUID | None,
    ) -> LearningRecordList:
        """分页读取属性，不读取笔记；时间、条数、页数和累计响应大小均有界。"""
        try:
            async with asyncio.timeout(_CATALOG_TIMEOUT_SECONDS):
                return await self._query_records(
                    token=token,
                    data_source_id=data_source_id,
                    fastapi_page_id=fastapi_page_id,
                )
        except TimeoutError:
            raise LearningSourceError("source_timeout", status_code=504) from None

    async def _query_records(
        self,
        *,
        token: str,
        data_source_id: UUID,
        fastapi_page_id: UUID | None,
    ) -> LearningRecordList:
        headers = _headers(token)
        items: list[LearningRecordSummary] = []
        record_ids: set[UUID] = set()
        cursors: set[str] = set()
        cursor = None
        remaining_bytes = _MAX_QUERY_BYTES
        warnings: list[str] = []
        has_more = False
        for _ in range(_MAX_QUERY_PAGES):
            page_size = min(_QUERY_PAGE_SIZE, _MAX_RECORDS - len(items))
            query: dict[str, object] = {
                "page_size": page_size,
                "result_type": "page",
                "is_archived": False,
                "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
            }
            if cursor is not None:
                query["start_cursor"] = cursor
            if remaining_bytes <= 0:
                raise LearningSourceError("source_too_large")
            payload = await fetch_source_bytes(
                self.client,
                f"{_NOTION_QUERY_API}/{data_source_id}/query",
                method="POST",
                json_body=query,
                headers=headers,
                max_bytes=min(1_500_000, remaining_bytes),
                sensitive_values=(token,),
            )
            remaining_bytes -= len(payload)
            data = _load_object(payload, token)
            results, has_more, next_cursor, incomplete = _query_page(data, page_size)
            if incomplete and "notion_query_incomplete" not in warnings:
                warnings.append("notion_query_incomplete")
            for page in results:
                record = _record_summary(
                    page,
                    data_source_id=data_source_id,
                    fastapi_page_id=fastapi_page_id,
                    detail=False,
                )
                if record.id in record_ids:
                    raise LearningSourceError("invalid_source_response")
                record_ids.add(record.id)
                items.append(record)
            if not has_more:
                break
            if next_cursor in cursors:
                raise LearningSourceError("invalid_source_response")
            cursors.add(next_cursor)
            cursor = next_cursor
            if len(items) >= _MAX_RECORDS:
                warnings.append("record_limit_reached")
                break
        else:
            warnings.append("notion_page_limit_reached")
        return LearningRecordList(
            source="notion",
            items=items,
            fetched_at=datetime.now(UTC),
            has_more=has_more or "notion_query_incomplete" in warnings,
            warnings=warnings,
        )

    async def read_record(
        self,
        *,
        token: str,
        data_source_id: UUID,
        page_id: UUID,
        fastapi_page_id: UUID | None,
    ) -> LearningRecordDetail:
        """只有页面属于允许的数据源且有效时，才请求其 Markdown 正文。"""
        try:
            async with asyncio.timeout(_CATALOG_TIMEOUT_SECONDS):
                headers = _headers(token)
                url = f"{_NOTION_API}/{page_id}"
                page = _load_object(
                    await fetch_source_bytes(
                        self.client, url, headers=headers, sensitive_values=(token,)
                    ),
                    token,
                )
                _validate_identity(page, "page", page_id)
                summary = _record_summary(
                    page,
                    data_source_id=data_source_id,
                    fastapi_page_id=fastapi_page_id,
                    detail=True,
                )
                content = _load_object(
                    await fetch_source_bytes(
                        self.client,
                        f"{url}/markdown",
                        headers=headers,
                        sensitive_values=(token,),
                    ),
                    token,
                )
                _validate_identity(content, "page_markdown", page_id)
                markdown, complete, warnings = _notes(content)
                return LearningRecordDetail(
                    **summary.model_dump(exclude={"fetched_at", "warnings"}),
                    fetched_at=datetime.now(UTC),
                    notes_markdown=markdown,
                    notes_complete=complete,
                    warnings=[*summary.warnings, *warnings],
                )
        except TimeoutError:
            raise LearningSourceError("source_timeout", status_code=504) from None
        except LearningSourceError as exc:
            if exc.code == "source_not_found":
                raise LearningSourceError(
                    "learning_record_not_found", status_code=404
                ) from None
            raise


def _headers(token: str) -> dict[str, str]:
    if not token or any(not 33 <= ord(char) <= 126 for char in token):
        raise LearningSourceError("source_unauthorized")
    return {"Authorization": f"Bearer {token}", "Notion-Version": _NOTION_VERSION}


def _validate_active(page: dict[str, object], *, detail: bool = False) -> None:
    for flag in ("archived", "in_trash", "is_archived"):
        if flag not in page:
            continue
        if type(page[flag]) is not bool:
            raise LearningSourceError("invalid_source_response")
        if page[flag]:
            if detail:
                raise LearningSourceError("learning_record_not_found", status_code=404)
            raise LearningSourceError("invalid_source_response")


def _page_properties(
    page: dict[str, object], field_map: dict[str, tuple[str, str]]
) -> tuple[dict[str, str | bool | None], list[str], datetime | None]:
    warnings: list[str] = []
    properties = page.get("properties")
    if not isinstance(properties, dict):
        raise LearningSourceError("source_schema_mismatch")
    values: dict[str, str | bool | None] = {}
    for field, (property_name, property_type) in field_map.items():
        if property_name not in properties:
            if field == "topic_name":
                raise LearningSourceError("source_schema_mismatch")
            values[field] = None
            warnings.append(f"missing_{field}")
            continue
        values[field] = _property_value(properties[property_name], property_type)
    if not values["topic_name"]:
        raise LearningSourceError("source_schema_mismatch")
    updated_at = _updated_at(page.get("last_edited_time"))
    if updated_at is None:
        warnings.append("missing_source_updated_at")
    return values, warnings, updated_at


def _record_summary(
    page: object,
    *,
    data_source_id: UUID,
    fastapi_page_id: UUID | None,
    detail: bool,
) -> LearningRecordSummary:
    if not isinstance(page, dict) or page.get("object") != "page":
        raise LearningSourceError("invalid_source_response")
    try:
        page_id = UUID(page["id"]) if isinstance(page.get("id"), str) else None
    except ValueError:
        raise LearningSourceError("invalid_source_response") from None
    if page_id is None:
        raise LearningSourceError("invalid_source_response")
    parent = page.get("parent")
    parent_id = None
    if isinstance(parent, dict) and parent.get("type") == "data_source_id":
        try:
            if isinstance(parent.get("data_source_id"), str):
                parent_id = UUID(parent["data_source_id"])
        except ValueError:
            raise LearningSourceError("invalid_source_response") from None
    if parent_id != data_source_id:
        if detail:
            raise LearningSourceError("learning_record_not_found", status_code=404)
        raise LearningSourceError("invalid_source_response")
    _validate_active(page, detail=detail)
    values, warnings, updated_at = _page_properties(page, _RECORD_FIELD_MAP)
    return LearningRecordSummary(
        id=page_id,
        topic_id="fastapi" if page_id == fastapi_page_id else None,
        **values,
        source="notion",
        source_url=f"https://www.notion.so/{page_id.hex}",
        source_updated_at=updated_at,
        fetched_at=datetime.now(UTC),
        warnings=warnings,
    )


def _query_page(
    data: dict[str, object], page_size: int
) -> tuple[list[object], bool, str | None, bool]:
    results = data.get("results")
    has_more = data.get("has_more")
    cursor = data.get("next_cursor")
    if (
        data.get("object") != "list"
        or not isinstance(results, list)
        or len(results) > page_size
        or type(has_more) is not bool
        or "next_cursor" not in data
    ):
        raise LearningSourceError("invalid_source_response")
    if has_more:
        if not isinstance(cursor, str) or not cursor or len(cursor) > 1_024:
            raise LearningSourceError("invalid_source_response")
    elif cursor is not None:
        raise LearningSourceError("invalid_source_response")
    request_status = data.get("request_status")
    incomplete = False
    if request_status is not None:
        if not isinstance(request_status, dict) or request_status.get("type") not in (
            "complete",
            "incomplete",
        ):
            raise LearningSourceError("invalid_source_response")
        incomplete = request_status["type"] == "incomplete"
    return results, has_more, cursor, incomplete


def _load_object(payload: bytes, token: str) -> dict[str, object]:
    try:
        result = json.loads(payload)
    except ValueError, UnicodeError, RecursionError:
        raise LearningSourceError("invalid_source_response") from None
    if not isinstance(result, dict):
        raise LearningSourceError("invalid_source_response")
    # HTTP 层检查原始字节；这里还需拒绝 JSON 转义后才出现的凭据回显。
    pending = [result]
    while pending:
        value = pending.pop()
        if isinstance(value, str) and token in value:
            raise LearningSourceError("invalid_source_response")
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return result


def _validate_identity(
    data: dict[str, object], object_type: str, page_id: UUID
) -> None:
    if data.get("object") != object_type or not isinstance(data.get("id"), str):
        raise LearningSourceError("invalid_source_response")
    try:
        identity = UUID(data["id"])
    except ValueError:
        raise LearningSourceError("invalid_source_response") from None
    if identity != page_id:
        raise LearningSourceError("invalid_source_response")


def _property_value(data: object, property_type: str) -> str | bool | None:
    if (
        not isinstance(data, dict)
        or data.get("type") != property_type
        or property_type not in data
    ):
        raise LearningSourceError("source_schema_mismatch")
    value = data[property_type]
    if property_type in ("title", "rich_text"):
        if not isinstance(value, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("plain_text"), str)
            for item in value
        ):
            raise LearningSourceError("source_schema_mismatch")
        return "".join(item["plain_text"] for item in value).strip() or None
    if property_type == "select":
        if value is None:
            return None
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            raise LearningSourceError("source_schema_mismatch")
        return value["name"].strip() or None
    if property_type == "checkbox":
        if type(value) is not bool:
            raise LearningSourceError("source_schema_mismatch")
        return value
    if value is not None and not isinstance(value, str):
        raise LearningSourceError("source_schema_mismatch")
    return value


def _updated_at(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LearningSourceError("invalid_source_response")
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
        return timestamp.astimezone(UTC)
    except ValueError, OverflowError:
        raise LearningSourceError("invalid_source_response") from None


def _notes(data: dict[str, object]) -> tuple[str, bool, list[str]]:
    markdown = data.get("markdown")
    truncated = data.get("truncated")
    unknown_blocks = data.get("unknown_block_ids")
    # Notion 仅在有警告时返回 warnings；另外两个完整性字段不能猜测默认值。
    upstream_warnings = data.get("warnings", [])
    if (
        not isinstance(markdown, str)
        or type(truncated) is not bool
        or not isinstance(unknown_blocks, list)
        or any(not isinstance(item, str) for item in unknown_blocks)
        or not isinstance(upstream_warnings, list)
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("code"), str)
            or not isinstance(item.get("message"), str)
            for item in upstream_warnings
        )
    ):
        raise LearningSourceError("invalid_source_response")
    warnings = []
    if truncated:
        warnings.append("notion_notes_truncated")
    if unknown_blocks:
        warnings.append("notion_notes_unknown_blocks")
    if upstream_warnings:
        warnings.append("notion_notes_warnings")
    if _UNKNOWN_BLOCK.search(markdown):
        warnings.append("notion_notes_unknown_markup")
    return markdown, not warnings, warnings
