"""普通聊天与学习 Agent 共用的、按请求隔离的 OpenRouter 模型工厂。"""

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_openrouter import ChatOpenRouter
from langsmith import tracing_context
from openrouter import OpenRouter
from openrouter.components import ChatResult as SDKChatResult
from openrouter.errors import OpenRouterError, ResponseValidationError
from openrouter.utils.logger import NoOpLogger
from pydantic import SecretStr, TypeAdapter, ValidationError

from ai_workspace.providers.errors import ProviderError
from ai_workspace.providers.types import ReasoningEffort, TokenUsage

_BASE_URL = "https://openrouter.ai/api/v1"
_TOKEN_COUNT = TypeAdapter(int)
_REASONING_EFFORT = TypeAdapter(ReasoningEffort)


class _WorkspaceChatOpenRouter(ChatOpenRouter):
    """在 LangChain 回调和工具执行之前校验响应并清除原始异常。"""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise ProviderError("Chat model requires asynchronous invocation")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # 工具绑定可以增加参数，但不能更改当前运行的凭据、网络与预算边界。
        kwargs.pop("max_completion_tokens", None)
        kwargs.pop("reasoning_effort", None)
        kwargs.pop("n", None)
        kwargs.pop("max_tokens", None)
        kwargs.pop("reasoning", None)
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if self.reasoning is not None:
            kwargs["reasoning"] = self.reasoning
        kwargs.update(
            server_url=_BASE_URL,
            retries=None,
            timeout_ms=self.request_timeout,
            stream=False,
            http_headers={
                "Authorization": f"Bearer {self.openrouter_api_key.get_secret_value()}"
            },
        )
        try:
            try:
                result = await super()._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs
                )
            except ResponseValidationError as exc:
                # SDK 要求 usage 的三项计数齐全；展示用量缺失或无效不应
                # 丢弃有效回答。只放宽 usage，其他字段仍由原 SDK 模型校验。
                if exc.status_code != 200:
                    raise
                payload = exc.raw_response.json()
                if not isinstance(payload, dict) or "usage" not in payload:
                    raise ProviderError(
                        "OpenRouter returned an invalid response"
                    ) from None
                usage = payload.pop("usage")
                validated = SDKChatResult.model_validate(payload)
                result = self._create_chat_result(
                    {
                        **validated.model_dump(by_alias=True, warnings=False),
                        "usage": usage,
                    }
                )
            if _contains_secret(
                result.model_dump(serialize_as_any=True),
                self.openrouter_api_key.get_secret_value(),
            ):
                raise ProviderError("OpenRouter returned an invalid response")
            return result
        except ProviderError:
            raise
        except OpenRouterError as exc:
            raise ProviderError(
                f"OpenRouter returned HTTP {exc.status_code}",
                status_code=exc.status_code,
            ) from None
        except Exception:  # noqa: BLE001 - 在第三方 SDK 边界统一清除敏感异常信息。
            # 取消属于 BaseException，仍会正常传播到上下文管理器并关闭客户端。
            raise ProviderError("OpenRouter request failed") from None

    def _create_chat_result(self, response: Any) -> ChatResult:
        payload = (
            response
            if isinstance(response, dict)
            else response.model_dump(by_alias=True, warnings=False)
        )
        choices = payload.get("choices")
        if (
            payload.get("error") is not None
            or not isinstance(payload.get("model"), str)
            or not payload["model"].strip()
            or not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
            or choices[0].get("finish_reason") not in ("stop", "tool_calls")
        ):
            raise ProviderError("OpenRouter returned an invalid response")
        usage = _map_usage(payload.get("usage"))
        # LangChain 默认会给缺失计数补零；只向它提供完整的已知计数。
        complete_usage = usage.model_dump() if usage is not None else None
        if complete_usage is not None and any(
            value is None for value in complete_usage.values()
        ):
            complete_usage = None
        result = super()._create_chat_result({**payload, "usage": complete_usage})
        for generation in result.generations:
            message = generation.message
            if not isinstance(message, AIMessage) or message.invalid_tool_calls:
                raise ProviderError("OpenRouter returned an invalid response")
            # Runner 汇总此字段以保留部分计数；不把未知项伪造为零。
            message.response_metadata["token_usage"] = (
                usage.model_dump() if usage is not None else None
            )
        return result


def _map_usage(raw: object) -> TokenUsage | None:
    if not isinstance(raw, dict):
        return None
    try:
        values = {
            key: _TOKEN_COUNT.validate_python(raw[key])
            if raw.get(key) is not None
            else None
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        return (
            TokenUsage(**values)
            if any(value is not None for value in values.values())
            else None
        )
    except ValidationError:
        return None


def _contains_secret(value: Any, secret: str) -> bool:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str) and secret in item:
            return True
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list | tuple):
            pending.extend(item)
    return False


@asynccontextmanager
async def open_chat_model(
    *,
    api_key: str,
    model: str,
    timeout_seconds: float = 60,
    max_tokens: int | None = None,
    reasoning_effort: ReasoningEffort | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[ChatOpenRouter]:
    """使用当前用户的密钥创建异步模型，退出时关闭客户端。

    普通聊天默认不限制输出 tokens；学习运行由调用者传入自己的预算。
    ``transport`` 仅供离线测试注入，不能用于选择上游地址。
    """
    if (
        not isinstance(api_key, str)
        or not api_key
        or any(not 33 <= ord(char) <= 126 for char in api_key)
    ):
        raise ProviderError("Invalid OpenRouter API key")
    if (
        not isinstance(model, str)
        or not model
        or len(model) > 255
        or any(not 33 <= ord(char) <= 126 for char in model)
        or api_key in model
    ):
        raise ProviderError("Invalid OpenRouter model")
    if (
        not isinstance(timeout_seconds, int | float)
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds < 0.001
        or (max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0))
    ):
        raise ProviderError("Invalid OpenRouter request limits")
    if reasoning_effort is not None:
        try:
            _REASONING_EFFORT.validate_python(reasoning_effort)
        except ValidationError:
            raise ProviderError("Invalid OpenRouter reasoning effort") from None
    timeout_ms = int(timeout_seconds * 1_000)

    async def validate_response(response: httpx.Response) -> None:
        # 在 SDK 丢弃未知字段前检查 HTTP 200 错误与所有字段中的密钥回显。
        if response.status_code != 200:
            return
        await response.aread()
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("error") is not None
            or _contains_secret(payload, api_key)
            or not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
            or choices[0].get("finish_reason") not in ("stop", "tool_calls")
        ):
            raise ProviderError("OpenRouter returned an invalid response")

    # SDK 不会关闭调用方提供的客户端，因此两者都由工厂显式管理。
    with httpx.Client(follow_redirects=False, trust_env=False) as sync_client:
        async with httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=timeout_seconds,
            event_hooks={"response": [validate_response]},
        ) as async_client:
            sdk = OpenRouter(
                api_key=api_key,
                server_url=_BASE_URL,
                client=sync_client,
                async_client=async_client,
                # 仅设置 ChatOpenRouter.max_retries=0 会恢复 SDK 默认重试。
                retry_config=None,
                timeout_ms=timeout_ms,
                debug_logger=NoOpLogger(),
                http_referer="",
                x_open_router_title="AI Workspace",
                x_open_router_categories="",
            )
            chat_model = _WorkspaceChatOpenRouter(
                api_key=SecretStr(api_key),
                model=model,
                base_url=_BASE_URL,
                client=sdk,
                timeout=timeout_ms,
                max_retries=0,
                max_tokens=max_tokens,
                reasoning={"effort": reasoning_effort}
                if reasoning_effort is not None
                else None,
                cache=False,
                streaming=False,
                disable_streaming=True,
                verbose=False,
                callbacks=[],
                app_url=None,
                app_title=None,
                session_id=None,
            )
            # 不允许宿主的 LangSmith 配置隐式外发私人会话与用户凭据。
            with tracing_context(enabled=False):
                yield chat_model
