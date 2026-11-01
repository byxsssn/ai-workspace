"""只读接入检查：无需模型调用或数据库，默认只输出验证摘要。"""

import argparse
import asyncio
import json
from uuid import UUID

from pydantic import ValidationError

from ai_workspace.learning.config import LearningSettings
from ai_workspace.learning.documents import SECTIONS
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.http import create_source_client
from ai_workspace.learning.service import LearningService


async def check(args: argparse.Namespace) -> dict[str, object]:
    settings = LearningSettings()
    # 命令行由本机操作者运行；Web 接口另行验证 JWT 和连接归属。
    local_user_id = settings.notion_owner_user_id or UUID(int=0)
    async with create_source_client() as client:
        service = LearningService(settings, client)
        state = await service.read_state(user_id=local_user_id, source=args.source)
        learning_map = None
        if args.map:
            records = await service.list_records(
                user_id=local_user_id, source=args.source
            )
            fastapi = next(
                (item for item in records.items if item.topic_id == "fastapi"), None
            )
            detail = (
                await service.read_record(
                    user_id=local_user_id,
                    source=args.source,
                    record_id=fastapi.id,
                )
                if fastapi is not None
                else None
            )
            learning_map = {
                "records": len(records.items),
                "has_more": records.has_more,
                "warnings": records.warnings,
                "fastapi_found": fastapi is not None,
                "detail_notes_characters": len(detail.notes_markdown)
                if detail is not None
                else None,
                "detail_notes_complete": detail.notes_complete
                if detail is not None
                else None,
            }
        section_ids = (
            [section.id for section in SECTIONS]
            if args.all_sections
            else [args.section]
        )
        documents = []
        for section_id in section_ids:
            document = await service.read_document(section_id)
            documents.append(
                {
                    "section_id": document.section_id,
                    "url": document.url,
                    "characters": len(document.content_markdown),
                    "contains_code_block": "```" in document.content_markdown,
                    "sha256": document.content_sha256,
                    "fetched_at": document.fetched_at.isoformat(),
                    "version": document.version,
                }
            )
        return {
            "source": state.source,
            "notes_characters": len(state.notes_markdown),
            "notes_complete": state.notes_complete,
            "warnings": state.warnings,
            **({"learning_map": learning_map} if learning_map is not None else {}),
            "documents": documents,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("demo", "notion"), default="demo")
    parser.add_argument(
        "--section",
        choices=tuple(section.id for section in SECTIONS),
        default="async-tests",
    )
    parser.add_argument("--all-sections", action="store_true")
    parser.add_argument(
        "--map",
        action="store_true",
        help="Also check the learning map and FastAPI detail",
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(check(args))
    except LearningSourceError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1
    except ValidationError:
        print("Invalid learning connection configuration; see docs/learning-sources.md")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
