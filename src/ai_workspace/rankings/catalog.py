"""人工维护的候选目录；排名只表示此目录内的下载量次序。"""

import re
from dataclasses import dataclass

CATALOG_VERSION = "2026-11-01.v1"
CATEGORIES = {
    "web": "Web 框架",
    "ai": "AI 与 Agent",
    "data": "数据处理",
    "testing": "测试工具",
    "engineering": "应用工程",
}


def normalize_project(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,198}[A-Za-z0-9])?", value
    ):
        raise ValueError("Invalid package name")
    return re.sub(r"[-_.]+", "-", value).lower()


@dataclass(frozen=True)
class CatalogEntry:
    project: str
    name: str
    category: str
    description: str


CATALOG = (
    CatalogEntry("fastapi", "FastAPI", "web", "基于类型提示的 Python Web API 框架"),
    CatalogEntry("django", "Django", "web", "包含 ORM、管理后台等组件的 Web 框架"),
    CatalogEntry("flask", "Flask", "web", "轻量 Python Web 框架"),
    CatalogEntry(
        "starlette", "Starlette", "web", "ASGI Web 框架与工具集，也是 FastAPI 的依赖"
    ),
    CatalogEntry("langchain", "LangChain", "ai", "模型与工具组成的 Agent 应用框架"),
    CatalogEntry(
        "langchain-core",
        "LangChain Core",
        "ai",
        "LangChain 核心接口，常作为其他包的依赖安装",
    ),
    CatalogEntry("langgraph", "LangGraph", "ai", "有状态 Agent 与工作流编排"),
    CatalogEntry(
        "llama-index", "LlamaIndex", "ai", "面向数据与检索增强应用的 Python 工具包"
    ),
    CatalogEntry("numpy", "NumPy", "data", "多维数组与数值计算"),
    CatalogEntry("pandas", "pandas", "data", "表格数据处理与分析"),
    CatalogEntry("polars", "Polars", "data", "DataFrame 数据处理"),
    CatalogEntry("scipy", "SciPy", "data", "科学计算与数值算法"),
    CatalogEntry("pytest", "pytest", "testing", "Python 自动化测试框架"),
    CatalogEntry("coverage", "coverage.py", "testing", "Python 测试代码覆盖率测量"),
    CatalogEntry("pydantic", "Pydantic", "engineering", "数据校验与序列化"),
    CatalogEntry("requests", "Requests", "engineering", "同步 HTTP 客户端"),
    CatalogEntry("httpx", "HTTPX", "engineering", "同步与异步 HTTP 客户端"),
    CatalogEntry("sqlalchemy", "SQLAlchemy", "engineering", "SQL 工具集与 ORM"),
    CatalogEntry("celery", "Celery", "engineering", "分布式任务队列"),
    CatalogEntry(
        "redis",
        "redis-py",
        "engineering",
        "Redis 的 Python 客户端；下载量不代表 Redis 数据库使用量",
    ),
)
