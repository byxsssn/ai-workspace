from typing import Annotated

from fastapi import Depends

from ai_workspace.agents.runner import AgentRunner
from ai_workspace.api.dependencies.learning import get_learning_service
from ai_workspace.learning.service import LearningService


def get_agent_runner(
    sources: Annotated[LearningService, Depends(get_learning_service)],
) -> AgentRunner:
    return AgentRunner(sources)
