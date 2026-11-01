from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from ai_workspace.learning.agent_models import LearningChatRequest
from ai_workspace.providers import ModelMessage, ReasoningEffort

if TYPE_CHECKING:
    from ai_workspace.services.turn import HistorySnapshot

_MAX_CONTEXT_MESSAGES = 20
_MAX_CONTEXT_CHARACTERS = 24_000


class LearningChatInputError(Exception):
    """Safe validation details for learning turns at either HTTP endpoint."""

    def __init__(self, errors: list[dict[str, object]]) -> None:
        super().__init__("Invalid learning chat input")
        self.errors = errors


def prepare_learning_messages(
    *,
    model: str,
    content: str,
    history: HistorySnapshot,
    source: Literal["demo", "notion"],
    reasoning_effort: ReasoningEffort | None = None,
) -> list[ModelMessage]:
    """Validate learning input and keep a bounded context without any I/O."""
    try:
        request = LearningChatRequest(model=model, content=content, source=source)
    except ValidationError as exc:
        raise LearningChatInputError(
            [
                {
                    "loc": ["body", *error["loc"]],
                    "type": error["type"],
                    "msg": "Invalid value",
                }
                for error in exc.errors(include_input=False, include_context=False)
            ]
        ) from None
    if reasoning_effort is not None:
        raise LearningChatInputError(
            [
                {
                    "loc": ["body", "reasoning_effort"],
                    "type": "value_error",
                    "msg": "Learning chat currently uses the model's default reasoning effort",
                }
            ]
        )
    return _bounded_context(history, request.content)


def _bounded_context(snapshot: HistorySnapshot, content: str) -> list[ModelMessage]:
    """Keep recent whole messages, including this request in both limits."""
    if len(content) > _MAX_CONTEXT_CHARACTERS:
        raise ValueError("Learning input exceeds the context limit")
    history = [
        ModelMessage(role=role, content=text)
        for _, role, text in snapshot
        if role in ("user", "assistant")
    ][-(_MAX_CONTEXT_MESSAGES - 1) :]
    history.append(ModelMessage(role="user", content=content))
    characters = sum(len(message.content) for message in history)
    while characters > _MAX_CONTEXT_CHARACTERS:
        characters -= len(history.pop(0).content)
    while history[0].role == "assistant":
        history.pop(0)
    return history
