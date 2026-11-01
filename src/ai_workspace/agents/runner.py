"""同一模型工厂、Agent 循环和资源生命周期；模式只决定工具与回答规则。"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Literal
from uuid import UUID

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRequest,
    ToolCallLimitMiddleware,
)
from langchain.agents.middleware import (
    ModelResponse as MiddlewareResponse,
)
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain.agents.structured_output import StructuredOutputError, ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError
from langsmith import tracing_context

from ai_workspace.learning.agent_errors import LearningAgentError
from ai_workspace.learning.agent_models import LearningAnswer, LearningChatResponse
from ai_workspace.learning.policy import LearningPolicy
from ai_workspace.learning.service import LearningService
from ai_workspace.providers import (
    ModelMessage,
    ModelResponse,
    ProviderError,
    ReasoningEffort,
    TokenUsage,
)
from ai_workspace.providers.openrouter import open_chat_model

type ModelFactory = Callable[..., AbstractAsyncContextManager[BaseChatModel]]


class _SecretGuard(AgentMiddleware):
    """已知凭据不能通过消息、工具参数或返回元数据进入下一步。"""

    def __init__(self, secrets: tuple[str, ...]) -> None:
        self.secrets = tuple(value for value in secrets if value)

    def check_payload(self, value: object) -> None:
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str) and any(secret in item for secret in self.secrets):
                raise ProviderError("Agent received an invalid payload")
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list | tuple):
                pending.extend(item)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[MiddlewareResponse]],
    ) -> MiddlewareResponse:
        self.check_payload([message.model_dump() for message in request.messages])
        result = await handler(request)
        self.check_payload([message.model_dump() for message in result.result])
        for message in result.result:
            if isinstance(message, AIMessage) and (
                message.invalid_tool_calls
                or message.response_metadata.get("finish_reason")
                not in (None, "stop", "tool_calls")
            ):
                raise ProviderError("Agent returned an incomplete response")
        return result


class AgentRunner:
    """一个请求一个 Agent；历史由数据库传入，工具身份由后端绑定。"""

    def __init__(
        self,
        sources: LearningService,
        *,
        model_factory: ModelFactory = open_chat_model,
        timeout_seconds: float | None = None,
    ) -> None:
        self.sources = sources
        self.model_factory = model_factory
        self.timeout_seconds = timeout_seconds

    async def run(
        self,
        *,
        user_id: UUID,
        api_key: str,
        model: str,
        messages: Sequence[ModelMessage],
        source: Literal["demo", "notion"] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> ModelResponse | LearningChatResponse:
        # 权限预检先于模型请求。普通对话不创建学习工具，也不要求 Notion 配置。
        policy = LearningPolicy(self.sources, user_id, source) if source else None
        secrets = (api_key,)
        if self.sources.settings.notion_api_token is not None:
            secrets += (self.sources.settings.notion_api_token.get_secret_value(),)
        guard = _SecretGuard(secrets)
        middleware = [
            ModelCallLimitMiddleware(
                run_limit=5 if policy else 1, exit_behavior="error"
            )
        ]
        if policy:
            middleware.append(
                ToolCallLimitMiddleware(run_limit=6, exit_behavior="error")
            )
        middleware.append(guard)
        timeout = self.timeout_seconds
        if timeout is None:
            timeout = 120 if policy else 60

        try:
            guard.check_payload([message.model_dump() for message in messages])
            guard.check_payload(model)
            # 统一禁止环境变量隐式开启云端追踪或共享对话缓存。
            with tracing_context(enabled=False):
                async with asyncio.timeout(timeout):
                    async with self.model_factory(
                        api_key=api_key,
                        model=model,
                        timeout_seconds=45 if policy else 60,
                        max_tokens=2_000 if policy else None,
                        reasoning_effort=reasoning_effort,
                    ) as llm:
                        agent = create_agent(
                            model=llm,
                            tools=policy.session.build_tools() if policy else [],
                            system_prompt=policy.system_prompt if policy else None,
                            response_format=ToolStrategy(
                                LearningAnswer, handle_errors=False
                            )
                            if policy
                            else None,
                            middleware=middleware,
                        )
                        result = await agent.ainvoke(
                            {
                                "messages": [
                                    message.model_dump() for message in messages
                                ]
                            },
                            config={"recursion_limit": 40, "callbacks": []},
                        )
            generated = [
                message
                for message in result["messages"][len(messages) :]
                if isinstance(message, AIMessage)
            ]
            if not generated:
                raise ProviderError("Agent did not return a response")
            actual_model = generated[-1].response_metadata.get("model_name", model)
            if not isinstance(actual_model, str) or not actual_model.strip():
                raise ProviderError("Agent returned an invalid model")
            usage = _sum_usage(generated)
            if policy:
                answer = result.get("structured_response")
                if not isinstance(answer, LearningAnswer):
                    raise LearningAgentError("learning_invalid_answer")
                guard.check_payload(answer.model_dump())
                return policy.build_response(answer, model=actual_model, usage=usage)
            final = generated[-1]
            if final.tool_calls or not final.text.strip():
                raise ProviderError("Agent did not return a valid text response")
            return ModelResponse(content=final.text, model=actual_model, usage=usage)
        except LearningAgentError:
            raise
        except TimeoutError:
            if policy:
                raise LearningAgentError("learning_agent_timeout") from None
            raise ProviderError("Chat model request timed out") from None
        except (
            ModelCallLimitExceededError,
            ToolCallLimitExceededError,
            GraphRecursionError,
        ):
            if policy:
                raise LearningAgentError("learning_agent_limit") from None
            raise ProviderError("Chat model exceeded the execution limit") from None
        except StructuredOutputError:
            if policy:
                raise LearningAgentError("learning_invalid_answer") from None
            raise ProviderError("Chat model returned an invalid response") from None
        except Exception:  # noqa: BLE001 - 不让 SDK 或框架异常携带私有请求内容离开此边界。
            if policy:
                raise LearningAgentError() from None
            raise ProviderError("Chat model request failed") from None


def _sum_usage(messages: Sequence[AIMessage]) -> TokenUsage | None:
    """按字段汇总本轮计数；任意一次缺失该字段，就保持未知。"""
    if not messages:
        return None
    values = {}
    for output_name, input_name in (
        ("prompt_tokens", "input_tokens"),
        ("completion_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        counts = []
        for message in messages:
            if "token_usage" in message.response_metadata:
                usage = message.response_metadata["token_usage"]
                counts.append(
                    usage.get(output_name) if isinstance(usage, dict) else None
                )
            else:
                counts.append((message.usage_metadata or {}).get(input_name))
        values[output_name] = (
            sum(counts)
            if all(type(value) is int and value >= 0 for value in counts)
            else None
        )
    return (
        TokenUsage(**values)
        if any(value is not None for value in values.values())
        else None
    )
