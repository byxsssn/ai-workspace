_MESSAGES = {
    "learning_agent_failed": "Learning agent request failed",
    "learning_agent_timeout": "Learning agent request timed out",
    "learning_agent_limit": "Learning agent call limit reached",
    "learning_invalid_citation": "Learning answer could not be verified against retrieved sources",
    "learning_sources_required": "Learning answer requires successfully retrieved sources",
    "learning_invalid_answer": "Learning agent did not return a valid answer",
}


class LearningAgentError(Exception):
    """只暴露固定错误，框架或供应商的原始异常不进入 HTTP 响应。"""

    def __init__(self, code: str = "learning_agent_failed") -> None:
        self.code = code if code in _MESSAGES else "learning_agent_failed"
        self.status_code = 504 if self.code == "learning_agent_timeout" else 502
        super().__init__(_MESSAGES[self.code])
