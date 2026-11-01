"""手动或由外部调度调用：uv run python scripts/refresh_pypi_rankings.py。"""

import asyncio
import json

from ai_workspace.db.session import engine
from ai_workspace.rankings.errors import RankingUnavailable
from ai_workspace.rankings.service import PypiRankingService


async def main() -> int:
    try:
        result = await PypiRankingService().list_rankings(force_refresh=True)
        print(
            json.dumps(
                {
                    "period": result.period,
                    "source_count": result.source_count,
                    "matched_count": result.matched_count,
                    "snapshot_id": result.snapshot_id,
                    "source_updated_at": result.source_updated_at.isoformat(),
                    "checked_at": result.checked_at.isoformat(),
                    "stale": result.stale,
                    "warnings": result.warnings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except RankingUnavailable:
        print("PyPI 排行榜暂时不可用；请检查迁移与网络后重试。")
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
