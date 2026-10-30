import json
import re
from datetime import UTC, datetime
from uuid import UUID

import httpx2

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
_NOTION_API = "https://api.notion.com/v1/pages"
_NOTION_VERSION = "2026-03-11"
_UNKNOWN_BLOCK = re.compile(r"<unknown(?:\s|/?>)", re.IGNORECASE)


class NotionLearningReader:
    """读取一个已授权的学习记录，不修改页面或共享客户端的凭据。"""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def read(self, *, token: str, page_id: UUID) -> LearningState:
        if not token or any(not 33 <= ord(char) <= 126 for char in token):
            raise LearningSourceError("source_unauthorized")
        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": _NOTION_VERSION,
        }
        url = f"{_NOTION_API}/{page_id}"
        page = _load_object(
            await fetch_source_bytes(
                self.client, url, headers=headers, sensitive_values=(token,)
            ),
            token,
        )
        _validate_identity(page, "page", page_id)
        for flag in ("archived", "in_trash", "is_archived"):
            if flag in page and (type(page[flag]) is not bool or page[flag]):
                raise LearningSourceError("invalid_source_response")

        warnings: list[str] = []
        properties = page.get("properties")
        if not isinstance(properties, dict):
            raise LearningSourceError("source_schema_mismatch")
        values: dict[str, str | bool | None] = {}
        for field, (property_name, property_type) in NOTION_FIELD_MAP.items():
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
