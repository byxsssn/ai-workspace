from collections.abc import Sequence
from typing import Protocol

from ai_workspace.providers.types import ModelMessage, ModelResponse, ProviderId


class LLMProvider(Protocol):
    """Generate text; adapters translate expected upstream failures to ProviderError."""

    @property
    def provider_id(self) -> ProviderId: ...

    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        messages: Sequence[ModelMessage],
    ) -> ModelResponse: ...
