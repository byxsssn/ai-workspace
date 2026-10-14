from collections.abc import Sequence
from json import JSONDecodeError

from openai import APIError, APIStatusError, AsyncOpenAI, DefaultAsyncHttpxClient, omit
from openai.types.responses import Response, ResponseInputParam, ResponseUsage
from pydantic import ValidationError

from ai_workspace.providers.errors import ProviderError
from ai_workspace.providers.types import (
    ModelMessage,
    ModelResponse,
    ProviderId,
    ReasoningEffort,
    TokenUsage,
)

_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider:
    """无状态文本生成，每次请求使用独立的异步 SDK 客户端。"""

    @property
    def provider_id(self) -> ProviderId:
        return ProviderId.OPENROUTER

    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        messages: Sequence[ModelMessage],
        reasoning_effort: ReasoningEffort | None = None,
    ) -> ModelResponse:
        """使用调用方解密后的密钥，请求结束后关闭 SDK 客户端。"""
        if not api_key or any(not 33 <= ord(char) <= 126 for char in api_key):
            raise ProviderError("Invalid OpenRouter API key")

        try:
            async with AsyncOpenAI(
                api_key=api_key,
                base_url=_BASE_URL,
                # SDK 环境配置中的请求头不得覆盖当前用户自带的密钥。
                default_headers={"Authorization": f"Bearer {api_key}"},
                http_client=DefaultAsyncHttpxClient(follow_redirects=False),
                timeout=60.0,
                max_retries=0,
            ) as client:
                response = await client.responses.create(
                    model=model,
                    input=_map_input(messages),
                    reasoning=(
                        {"effort": reasoning_effort}
                        if reasoning_effort is not None
                        else omit
                    ),
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
            # SDK 异常可能包含上游响应体或凭据。
            raise ProviderError("OpenRouter request failed") from None

        try:
            # SDK 采用宽松解析；此处只校验项目所需的文本结果，
            # 同时检查 OpenRouter 可能在 HTTP 200 响应中返回的错误。
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
            # 即使上游回显了密钥，也不能将其暴露给调用方。
            if api_key in result.content or api_key in result.model:
                raise ValueError
            return result
        except AttributeError, TypeError, ValueError:
            # 映射和校验异常可能包含上游数据。
            raise ProviderError("OpenRouter returned an invalid response") from None


def _map_input(messages: Sequence[ModelMessage]) -> ResponseInputParam:
    """重建完整文本历史，不依赖供应商侧状态。"""
    items: ResponseInputParam = []
    for index, message in enumerate(messages):
        if message.role == "assistant":
            # OpenRouter 要求助手历史包含 id/status；这些 ID 仅标记本次请求中的消息，
            # 实际历史始终以 PostgreSQL 中的记录为准。
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
    """可选的展示用量有误时，仍保留有效回答。"""
    if not isinstance(usage, ResponseUsage):
        return None
    try:
        return TokenUsage(
            prompt_tokens=getattr(usage, "input_tokens", None),
            completion_tokens=getattr(usage, "output_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
        )
    except ValidationError:
        # 接受 SDK 已完成的数值规范化，不补造缺失或无效的计数。
        return None
