from collections.abc import Sequence
from typing import Protocol

from ai_workspace.providers.types import (
    ModelMessage,
    ModelResponse,
    ProviderId,
    ReasoningEffort,
)


class LLMProvider(Protocol):
    """生成文本；适配器将预期的上游失败转换为 ProviderError。"""

    @property
    def provider_id(self) -> ProviderId: ...

    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        messages: Sequence[ModelMessage],
        reasoning_effort: ReasoningEffort | None = None,
    ) -> ModelResponse: ...
