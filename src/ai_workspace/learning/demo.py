from datetime import UTC, datetime

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
