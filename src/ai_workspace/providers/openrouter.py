from collections.abc import Sequence
from json import JSONDecodeError

from openai import APIError, APIStatusError, AsyncOpenAI, DefaultAsyncHttpxClient
from openai.types.responses import Response, ResponseInputParam, ResponseUsage
from pydantic import ValidationError

from ai_workspace.providers.errors import ProviderError
from ai_workspace.providers.types import (
    ModelMessage,
    ModelResponse,
    ProviderId,
    TokenUsage,
)

_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider:
    """Stateless text responses with a request-scoped async SDK client."""

    @property
    def provider_id(self) -> ProviderId:
        return ProviderId.OPENROUTER

    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        messages: Sequence[ModelMessage],
    ) -> ModelResponse:
        """Use the caller's decrypted key and close the SDK client after the call."""
        if not api_key or any(not 33 <= ord(char) <= 126 for char in api_key):
            raise ProviderError("Invalid OpenRouter API key")

        try:
            async with AsyncOpenAI(
                api_key=api_key,
                base_url=_BASE_URL,
                # Environment-provided SDK headers must not override BYOK.
                default_headers={"Authorization": f"Bearer {api_key}"},
                http_client=DefaultAsyncHttpxClient(follow_redirects=False),
                timeout=60.0,
                max_retries=0,
            ) as client:
                response = await client.responses.create(
                    model=model,
                    input=_map_input(messages),
                    store=False,
                    stream=False,
                )
        except APIStatusError as exc:
            raise ProviderError(
                f"OpenRouter returned HTTP {exc.status_code}",
                status_code=exc.status_code,
            ) from None
        except JSONDecodeError:
            raise ProviderError("OpenRouter returned an invalid response") from None
        except APIError, UnicodeError:
            # SDK exceptions may contain the upstream body or credentials.
            raise ProviderError("OpenRouter request failed") from None

        try:
            # SDK parsing is permissive; enforce only our text-result contract
            # and OpenRouter's errors that may arrive in an HTTP 200 response.
            if (
                not isinstance(response, Response)
                or response.status != "completed"
                or response.error is not None
                or response.incomplete_details is not None
            ):
                raise ValueError

            result = ModelResponse(
                content=response.output_text,
                model=response.model,
                usage=_map_usage(response.usage),
            )
            # Do not expose credentials even if an upstream response echoes them.
            if api_key in result.content or api_key in result.model:
                raise ValueError
            return result
        except AttributeError, TypeError, ValueError:
            # Mapping and validation exceptions can include upstream data.
            raise ProviderError("OpenRouter returned an invalid response") from None


def _map_input(messages: Sequence[ModelMessage]) -> ResponseInputParam:
    """Rebuild full text history without referring to provider-side state."""
    items: ResponseInputParam = []
    for index, message in enumerate(messages):
        if message.role == "assistant":
            # OpenRouter requires id/status for assistant history. These IDs only
            # identify items in this request; PostgreSQL owns the actual history.
            items.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "id": f"msg_{index}",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": message.content,
                            "annotations": [],
                        }
                    ],
                }
            )
        else:
            items.append(
                {
                    "type": "message",
                    "role": message.role,
                    "content": [{"type": "input_text", "text": message.content}],
                }
            )
    return items


def _map_usage(usage: ResponseUsage | None) -> TokenUsage | None:
    """Keep optional display statistics from making a valid answer fail."""
    if not isinstance(usage, ResponseUsage):
        return None
    try:
        return TokenUsage(
            prompt_tokens=getattr(usage, "input_tokens", None),
            completion_tokens=getattr(usage, "output_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
        )
    except ValidationError:
        # Accept SDK normalization, but never invent missing or invalid counts.
        return None
