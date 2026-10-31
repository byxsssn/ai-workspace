"""为异步学习 Agent 创建仅在当前请求内使用的 OpenRouter 模型。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_openrouter import ChatOpenRouter
from langsmith import tracing_context
from openrouter import OpenRouter
from openrouter.errors import OpenRouterError
from openrouter.utils.logger import NoOpLogger
from pydantic import SecretStr

from ai_workspace.providers.errors import ProviderError

_BASE_URL = "https://openrouter.ai/api/v1"
_TIMEOUT_MS = 45_000
_MAX_TOKENS = 2_000


class _LearningChatOpenRouter(ChatOpenRouter):
    """在 LangChain 触发回调之前，将 SDK 错误转换为不含敏感数据的异常。"""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise ProviderError("Learning model requires asynchronous invocation")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # SDK 调用参数可以覆盖客户端默认值；绑定工具或其他调用选项时，
        # 仍需固定目标地址、重试、超时和输出上限。
        kwargs.update(
            server_url=_BASE_URL,
            retries=None,
            timeout_ms=_TIMEOUT_MS,
            max_tokens=_MAX_TOKENS,
            stream=False,
        )
        try:
            result = await super()._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
            # 检查解析后的正文、工具参数和元数据，防止上游回显的密钥
            # 进入回调或后续工具执行流程。
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
            # 第三方请求或响应解析异常可能包含响应体和请求头。
            # 取消异常属于 BaseException，仍会正常向上传播。
            raise ProviderError("OpenRouter request failed") from None


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
async def open_learning_model(
    *,
    api_key: str,
    model: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[ChatOpenRouter]:
    """使用调用方自带的密钥完成一次 Agent 运行，退出时关闭两个 SDK 客户端。

    ``transport`` 仅用于注入离线测试传输层，不允许调用方指定上游地址。
    必须在此上下文退出前通过 ``ainvoke`` 使用模型。
    """
    if not api_key or any(not 33 <= ord(char) <= 126 for char in api_key):
        raise ProviderError("Invalid OpenRouter API key")
    if (
        not model
        or len(model) > 255
        or any(not 33 <= ord(char) <= 126 for char in model)
        or api_key in model
    ):
        raise ProviderError("Invalid OpenRouter model")

    # SDK 要求同时提供两个客户端，本模型仅支持异步调用。
    # SDK 上下文不会关闭外部传入的客户端，因此由本工厂显式管理两者。
    with httpx.Client(follow_redirects=False, trust_env=False) as sync_client:
        async with httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=_TIMEOUT_MS / 1_000,
        ) as async_client:
            sdk = OpenRouter(
                api_key=api_key,
                server_url=_BASE_URL,
                client=sync_client,
                async_client=async_client,
                # 仅设置 ChatOpenRouter 的 max_retries=0 仍会启用 SDK 默认重试。
                retry_config=None,
                timeout_ms=_TIMEOUT_MS,
                debug_logger=NoOpLogger(),
                http_referer="",
                x_open_router_title="AI Workspace",
                x_open_router_categories="",
            )
            chat_model = _LearningChatOpenRouter(
                api_key=SecretStr(api_key),
                model=model,
                base_url=_BASE_URL,
                client=sdk,
                timeout=_TIMEOUT_MS,
                max_retries=0,
                max_tokens=_MAX_TOKENS,
                cache=False,
                streaming=False,
                disable_streaming=True,
                verbose=False,
                callbacks=[],
                app_url=None,
                app_title=None,
                session_id=None,
            )
            # 禁止机器上的 LangSmith 环境变量隐式启用追踪，
            # 避免当前用户的密钥及私有学习上下文被额外发送到追踪服务。
            with tracing_context(enabled=False):
                yield chat_model
