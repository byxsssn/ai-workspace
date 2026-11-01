from functools import lru_cache

from ai_workspace.rankings.service import PypiRankingService


@lru_cache
def get_pypi_ranking_service() -> PypiRankingService:
    return PypiRankingService()
