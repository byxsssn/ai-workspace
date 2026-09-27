from collections.abc import Sequence

import httpx2

from ai_workspace.providers.errors import ProviderError
from ai_workspace.providers.types import ChatCompletionResponse, ChatMessage

_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider:
    """Non-streaming text completions using a caller-owned async HTTP client."""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self._client = client

    async def chat_completion(
        self,
        *,
        api_key: str,
        model: str,
        messages: Sequence[ChatMessage],
    ) -> ChatCompletionResponse:
        """Use a decrypted key for this request only; the caller closes the client."""
        if not api_key or any(not 33 <= ord(char) <= 126 for char in api_key):
            raise ProviderError("Invalid OpenRouter API key")

        try:
            response = await self._client.post(
                _CHAT_COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [message.model_dump() for message in messages],
                    "stream": False,
                },
                auth=None,
                follow_redirects=False,
                timeout=60.0,
            )
        except httpx2.HTTPError, UnicodeError:
            # Transport errors may contain headers or other sensitive input.
            raise ProviderError("OpenRouter request failed") from None

        if not response.is_success:
            raise ProviderError(
                f"OpenRouter returned HTTP {response.status_code}",
                status_code=response.status_code,
            )

        try:
            data = response.json()
            if not isinstance(data, dict) or data.get("error") is not None:
                raise ValueError
            choices = data["choices"]
            if not isinstance(choices, list) or not choices:
                raise ValueError
            choice = choices[0]
            if (
                not isinstance(choice, dict)
                or choice.get("error") is not None
                or choice.get("finish_reason") == "error"
            ):
                raise ValueError

            result = ChatCompletionResponse(
                content=choice["message"]["content"],
                model=data["model"],
                usage=data.get("usage"),
            )
            # Do not expose credentials even if an upstream response echoes them.
            if api_key in result.content or api_key in result.model:
                raise ValueError
            return result
        except KeyError, TypeError, ValueError:
            # JSON and validation exceptions can include the upstream body.
            raise ProviderError("OpenRouter returned an invalid response") from None
