import type { PypiRanking } from "./types";

export const rankingFixture: PypiRanking = {
  snapshot_id: "a".repeat(64), source: "hugovk/top-pypi-packages", upstream: "ClickHouse",
  source_url: "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json",
  period: "2026-09", source_updated_at: "2026-10-01T12:41:32Z",
  fetched_at: "2026-11-01T08:00:00Z", checked_at: "2026-11-01T08:00:00Z",
  catalog_version: "test-v1", candidate_count: 5, matched_count: 4,
  source_count: 15000, filtered_count: 5, stale: false, warnings: [],
  categories: [{ id: "web", label: "Web 框架" }, { id: "ai", label: "AI 与 Agent" }],
  items: [
    { project: "fastapi", name: "FastAPI", category: "web", category_label: "Web 框架", description: "异步 Web API 框架", download_count: 400000000, rank: 1, pypi_url: "https://pypi.org/project/fastapi/" },
    { project: "langchain", name: "LangChain", category: "ai", category_label: "AI 与 Agent", description: "模型与工具集成", download_count: 170000000, rank: 2, pypi_url: "https://pypi.org/project/langchain/" },
    { project: "flask", name: "Flask", category: "web", category_label: "Web 框架", description: "轻量 Web 框架", download_count: 100000000, rank: 3, pypi_url: "https://pypi.org/project/flask/" },
    { project: "django", name: "Django", category: "web", category_label: "Web 框架", description: "全栈 Web 框架", download_count: 40000000, rank: 4, pypi_url: "https://pypi.org/project/django/" },
    { project: "llama-index", name: "LlamaIndex", category: "ai", category_label: "AI 与 Agent", description: "检索与数据集成", download_count: null, rank: null, pypi_url: "https://pypi.org/project/llama-index/" },
  ],
};
