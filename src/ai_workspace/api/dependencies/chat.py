from typing import Annotated

from fastapi import Depends

from ai_workspace.agents.aa_client import AAClient
from ai_workspace.agents.aa_config import get_aa_settings
from ai_workspace.agents.runner import AgentRunner
from ai_workspace.api.dependencies.learning import get_learning_service
from ai_workspace.api.dependencies.rankings import get_pypi_ranking_service
from ai_workspace.learning.service import LearningService
from ai_workspace.rankings.service import PypiRankingService


def get_aa_client() -> AAClient | None:
    settings = get_aa_settings()
    return AAClient(settings) if settings.enabled else None


def get_agent_runner(
    sources: Annotated[LearningService, Depends(get_learning_service)],
    rankings: Annotated[PypiRankingService, Depends(get_pypi_ranking_service)],
    aa_client: Annotated[AAClient | None, Depends(get_aa_client)],
) -> AgentRunner:
    return AgentRunner(sources, rankings=rankings, aa_client=aa_client)
