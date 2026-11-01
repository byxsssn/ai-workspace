from datetime import UTC, datetime
from uuid import UUID

from ai_workspace.learning.catalog_models import (
    LearningRecordDetail,
    LearningRecordList,
    LearningRecordSummary,
)
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.models import LearningState


def demo_learning_state() -> LearningState:
    """固定虚构样例；不读取、不备份用户的私人 Notion 内容。"""
    return LearningState(
        topic_name="FastAPI（虚构演示记录）",
        source="demo",
        fetched_at=datetime.now(UTC),
        learning_status="学习中",
        current_progress="已阅读 Testing 的基础示例，尚未完成独立练习。",
        next_step="阅读 Async Tests，比较同步与异步测试的使用场景。",
        proficiency="看过",
        project_practice=False,
        official_docs_url="https://fastapi.tiangolo.com/",
        notes_markdown=(
            "## 演示笔记\n\n这是虚构的学习记录，不代表任何用户的真实进度。\n\n"
            "- 阅读内容：基础测试示例。\n"
            "- 待解决问题：异步数据库操作如何放进测试？\n"
            "- 实践证据：尚未提供。\n"
        ),
        notes_complete=True,
        warnings=["demo_data_not_personal_progress"],
    )


DEMO_FASTAPI_RECORD_ID = UUID("00000000-0000-4000-8000-000000000001")


def _demo_records() -> list[LearningRecordDetail]:
    """固定虚构主题与 ID，不把演示掌握程度解释为用户的真实能力。"""
    state = demo_learning_state()
    first = LearningRecordDetail(
        id=DEMO_FASTAPI_RECORD_ID,
        category="后端框架",
        priority="P0",
        **state.model_dump(),
    )
    return [
        first,
        LearningRecordDetail(
            id=UUID("00000000-0000-4000-8000-000000000002"),
            topic_name="Python（虚构演示记录）",
            category="编程语言",
            priority="P0",
            source="demo",
            fetched_at=state.fetched_at,
            learning_status="学习中",
            current_progress="虚构样例：已阅读函数和异常处理。",
            next_step="用一个小练习检验异常处理。",
            proficiency="练过",
            project_practice=True,
            official_docs_url="https://docs.python.org/3/tutorial/",
            notes_markdown="## 虚构演示笔记\n\n这不是用户的真实进度，也没有真实成果证据。",
            notes_complete=True,
            warnings=["demo_data_not_personal_progress"],
        ),
        LearningRecordDetail(
            id=UUID("00000000-0000-4000-8000-000000000003"),
            topic_name="PostgreSQL（虚构演示记录）",
            category="数据库",
            priority="P1",
            source="demo",
            fetched_at=state.fetched_at,
            learning_status="未开始",
            next_step="阅读索引介绍并完成一个查询练习。",
            project_practice=False,
            official_docs_url="https://www.postgresql.org/docs/",
            notes_markdown="## 虚构演示笔记\n\n尚未记录学习成果。",
            notes_complete=True,
            warnings=["demo_data_not_personal_progress"],
        ),
    ]


def demo_learning_records() -> LearningRecordList:
    items = [
        LearningRecordSummary.model_validate(
            item.model_dump(exclude={"notes_markdown", "notes_complete"})
        )
        for item in _demo_records()
    ]
    return LearningRecordList(
        source="demo",
        items=items,
        fetched_at=datetime.now(UTC),
        has_more=False,
        warnings=["demo_data_not_personal_progress"],
    )


def demo_learning_record(record_id: UUID) -> LearningRecordDetail:
    for record in _demo_records():
        if record.id == record_id:
            return record
    raise LearningSourceError("learning_record_not_found", status_code=404)
