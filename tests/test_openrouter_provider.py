import asyncio
import json
import logging
import traceback

import httpx
import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langsmith.run_helpers import get_tracing_context

from ai_workspace.providers.errors import ProviderError
from ai_workspace.providers.openrouter import open_chat_model

API_KEY = "sk-or-fabricated-learning-key"
MODEL = "example/fabricated-model"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def completion(*, content: str = "Use AsyncClient.", tool_calls=None) -> dict:
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "gen-fabricated",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": MODEL,
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "message": message,
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


class ClosingTransport(httpx.MockTransport):
    closed = False

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


def test_actual_chat_request_ignores_environment_and_closes_clients(monkeypatch):
    for name, value in {
        "OPENROUTER_API_KEY": "sk-or-wrong-global-key",
        "OPENROUTER_API_BASE": "https://wrong.example",
        "OPENROUTER_SESSION_ID": "wrong-global-session",
        "OPENROUTER_HTTP_REFERER": "https://wrong.example",
        "OPENROUTER_X_OPEN_ROUTER_TITLE": "wrong-global-title",
        "OPENROUTER_X_OPEN_ROUTER_CATEGORIES": "wrong-global-category",
        "HTTP_PROXY": "http://wrong.example:1234",
        "HTTPS_PROXY": "http://wrong.example:1234",
        "LANGSMITH_TRACING": "true",
    }.items():
        monkeypatch.setenv(name, value)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=completion())

    transport = ClosingTransport(respond)

    async def run():
        async with open_chat_model(
            api_key=API_KEY, model=MODEL, transport=transport
        ) as model:
            config = model.client.sdk_configuration
            sync_client, async_client = config.client, config.async_client
            assert config.retry_config is None
            assert not sync_client.is_closed and not async_client.is_closed
            assert get_tracing_context()["enabled"] is False
            assert model.cache is False
            assert API_KEY not in repr(model)
            assert API_KEY not in model.model_dump_json()
            first = await model.ainvoke([HumanMessage(content="How do I test async?")])
            second = await model.ainvoke("How do I test async?")
            assert first.content == second.content == "Use AsyncClient."
            assert first.usage_metadata["total_tokens"] == 15
        assert sync_client.is_closed and async_client.is_closed

    asyncio.run(run())
    assert transport.closed
    assert len(requests) == 2  # No global cache reuse.
    for request in requests:
        assert str(request.url) == ENDPOINT
        assert request.method == "POST"
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        assert "wrong-global" not in str(request.headers)
        payload = json.loads(request.content)
        assert payload["model"] == MODEL
        assert "max_tokens" not in payload
        assert "reasoning" not in payload
        assert payload["stream"] is False
        assert "session_id" not in payload
        assert request.extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), 60.0
        )


def test_tool_call_and_tool_result_round_trip():
    requests = []

    @tool
    def read_official_doc(section_id: str) -> str:
        """Read one approved official documentation section."""
        return "AsyncClient is asynchronous."

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=completion(
                    content="",
                    tool_calls=[
                        {
                            "id": "call-fabricated",
                            "type": "function",
                            "function": {
                                "name": "read_official_doc",
                                "arguments": '{"section_id":"async-tests"}',
                            },
                        }
                    ],
                ),
            )
        return httpx.Response(200, json=completion())

    async def run():
        async with open_chat_model(
            api_key=API_KEY, model=MODEL, transport=httpx.MockTransport(respond)
        ) as model:
            bound = model.bind_tools([read_official_doc])
            human = HumanMessage(content="Explain async tests.")
            answer = await bound.ainvoke([human])
            assert answer.tool_calls == [
                {
                    "name": "read_official_doc",
                    "args": {"section_id": "async-tests"},
                    "id": "call-fabricated",
                    "type": "tool_call",
                }
            ]
            final = await bound.ainvoke(
                [
                    human,
                    answer,
                    ToolMessage(
                        content="AsyncClient is asynchronous.",
                        tool_call_id="call-fabricated",
                    ),
                ]
            )
            assert final.content == "Use AsyncClient."

    asyncio.run(run())
    assert requests[0]["tools"][0]["function"]["name"] == "read_official_doc"
    assert requests[1]["messages"][-1] == {
        "role": "tool",
        "content": "AsyncClient is asynchronous.",
        "tool_call_id": "call-fabricated",
    }


@pytest.mark.parametrize("status", [301, 307, 401, 403, 429, 500, 503])
def test_failures_do_not_retry_redirect_or_expose_upstream_data(
    status, monkeypatch, caplog
):
    monkeypatch.setenv("OPENROUTER_DEBUG", "true")
    caplog.set_level(logging.DEBUG)
    requests = []
    callback_errors = []
    private_detail = "private-upstream-failure"

    class ErrorRecorder(BaseCallbackHandler):
        def on_llm_error(self, error, **kwargs):
            callback_errors.append(error)

    def respond(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"location": "https://wrong.example", "x-request-id": API_KEY},
            json={"error": {"message": private_detail + API_KEY, "code": status}},
        )

    transport = ClosingTransport(respond)

    async def run():
        with pytest.raises(ProviderError) as caught:
            async with open_chat_model(
                api_key=API_KEY, model=MODEL, transport=transport
            ) as model:
                await model.ainvoke("Hello", config={"callbacks": [ErrorRecorder()]})
        assert API_KEY not in str(caught.value)
        assert private_detail not in "".join(traceback.format_exception(caught.value))
        assert caught.value.status_code == status

    asyncio.run(run())
    assert len(requests) == 1
    assert transport.closed
    assert len(callback_errors) == 1
    assert isinstance(callback_errors[0], ProviderError)
    assert API_KEY not in repr(callback_errors)
    assert API_KEY not in caplog.text
    assert private_detail not in caplog.text


@pytest.mark.parametrize(
    "failure", ["timeout", "connection", "invalid_json", "invalid_schema"]
)
def test_transport_and_response_failures_are_safe_and_close(failure):
    requests = []

    def respond(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout(API_KEY, request=request)
        if failure == "connection":
            raise httpx.ConnectError(API_KEY, request=request)
        if failure == "invalid_json":
            return httpx.Response(
                200, text=API_KEY, headers={"content-type": "application/json"}
            )
        return httpx.Response(200, json={"unexpected": API_KEY})

    transport = ClosingTransport(respond)

    async def run():
        with pytest.raises(ProviderError) as caught:
            async with open_chat_model(
                api_key=API_KEY, model=MODEL, transport=transport
            ) as model:
                await model.ainvoke("Hello")
        assert API_KEY not in "".join(traceback.format_exception(caught.value))

    asyncio.run(run())
    assert len(requests) == 1
    assert transport.closed


@pytest.mark.parametrize("location", ["content", "metadata", "tool_arguments"])
def test_upstream_credential_echo_never_reaches_callbacks(location):
    payload = completion(content=API_KEY if location == "content" else "Answer")
    if location == "metadata":
        payload["model"] = API_KEY
    if location == "tool_arguments":
        payload["choices"][0]["message"]["tool_calls"] = [
            {
                "id": "call-fabricated",
                "type": "function",
                "function": {
                    "name": "read_official_doc",
                    "arguments": json.dumps({"section_id": API_KEY}),
                },
            }
        ]
    completed = []

    class ResultRecorder(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            completed.append(response)

    async def run():
        with pytest.raises(ProviderError, match="invalid response"):
            async with open_chat_model(
                api_key=API_KEY,
                model=MODEL,
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=payload)
                ),
            ) as model:
                await model.ainvoke("Hello", config={"callbacks": [ResultRecorder()]})

    asyncio.run(run())
    assert completed == []


def test_concurrent_requests_keep_distinct_credentials_and_clients():
    requests = []
    clients = []
    arrived = 0

    async def run():
        both_started = asyncio.Event()

        async def respond(request):
            nonlocal arrived
            requests.append(request)
            arrived += 1
            if arrived == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=2)
            return httpx.Response(200, json=completion())

        async def generate(key, question):
            async with open_chat_model(
                api_key=key, model=MODEL, transport=httpx.MockTransport(respond)
            ) as model:
                clients.append(model.client.sdk_configuration.async_client)
                return await model.ainvoke(question)

        await asyncio.gather(
            generate("sk-or-fabricated-a", "Question A"),
            generate("sk-or-fabricated-b", "Question B"),
        )

    asyncio.run(run())
    assert clients[0] is not clients[1]
    assert all(client.is_closed for client in clients)
    assert {
        (
            request.headers["authorization"],
            json.loads(request.content)["messages"][0]["content"],
        )
        for request in requests
    } == {
        ("Bearer sk-or-fabricated-a", "Question A"),
        ("Bearer sk-or-fabricated-b", "Question B"),
    }


def test_cancellation_propagates_and_closes_clients():
    transport = None

    async def run():
        nonlocal transport
        started = asyncio.Event()

        async def respond(request):
            started.set()
            await asyncio.Event().wait()

        transport = ClosingTransport(respond)

        async def generate():
            async with open_chat_model(
                api_key=API_KEY, model=MODEL, transport=transport
            ) as model:
                await model.ainvoke("Hello")

        task = asyncio.create_task(generate())
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert transport.closed


@pytest.mark.parametrize(
    "key", ["", "key with spaces", "key\r\nInjected: header", "密钥"]
)
def test_invalid_credentials_never_reach_transport(key):
    def respond(request):
        pytest.fail("Invalid credentials must not make an HTTP request")

    async def run():
        with pytest.raises(ProviderError, match="Invalid OpenRouter API key"):
            async with open_chat_model(
                api_key=key, model=MODEL, transport=httpx.MockTransport(respond)
            ):
                pytest.fail("Invalid credentials must not yield a model")

    asyncio.run(run())


def test_sync_invocation_fails_without_network():
    async def run():
        async with open_chat_model(api_key=API_KEY, model=MODEL) as model:
            with pytest.raises(ProviderError, match="asynchronous"):
                model.invoke("Hello")

    asyncio.run(run())


def test_invocation_options_cannot_override_network_boundaries():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=completion())

    async def run():
        async with open_chat_model(
            api_key=API_KEY,
            model=MODEL,
            timeout_seconds=45,
            max_tokens=2000,
            transport=httpx.MockTransport(respond),
        ) as model:
            await model.bind(
                server_url="https://wrong.example",
                timeout_ms=600_000,
                max_tokens=100_000,
                stream=True,
            ).ainvoke("Hello")

    asyncio.run(run())
    assert str(requests[0].url) == ENDPOINT
    assert requests[0].extensions["timeout"]["read"] == 45.0
    assert json.loads(requests[0].content)["max_tokens"] == 2_000
    assert json.loads(requests[0].content)["stream"] is False


@pytest.mark.parametrize("model_name", ["", "a" * 256, "model\r\n", API_KEY])
def test_invalid_model_never_reaches_transport(model_name):
    def respond(request):
        pytest.fail("Invalid model must not make an HTTP request")

    async def run():
        with pytest.raises(ProviderError, match="Invalid OpenRouter model"):
            async with open_chat_model(
                api_key=API_KEY,
                model=model_name,
                transport=httpx.MockTransport(respond),
            ):
                pytest.fail("Invalid model must not yield a client")

    asyncio.run(run())


def test_caller_failure_propagates_closes_clients_and_restores_tracing():
    async def run():
        previous = get_tracing_context()["enabled"]
        with pytest.raises(RuntimeError, match="caller failed"):
            async with open_chat_model(
                api_key=API_KEY,
                model=MODEL,
                transport=httpx.MockTransport(
                    lambda request: pytest.fail("No request expected")
                ),
            ) as model:
                config = model.client.sdk_configuration
                clients = (config.client, config.async_client)
                raise RuntimeError("caller failed")
        assert all(client.is_closed for client in clients)
        assert get_tracing_context()["enabled"] is previous

    asyncio.run(run())


@pytest.mark.parametrize(
    "effort", [None, "none", "minimal", "low", "medium", "high", "xhigh", "max"]
)
def test_reasoning_options_are_sent_without_loss(effort):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    async def run():
        async with open_chat_model(
            api_key=API_KEY,
            model=MODEL,
            reasoning_effort=effort,
            transport=httpx.MockTransport(respond),
        ) as model:
            await model.bind(reasoning={"effort": "wrong"}).ainvoke("Hello")

    asyncio.run(run())
    if effort is None:
        assert "reasoning" not in requests[0]
    else:
        assert requests[0]["reasoning"] == {"effort": effort}


@pytest.mark.parametrize(
    ("raw_usage", "expected"),
    [
        (None, None),
        ({}, None),
        (
            {"prompt_tokens": 10},
            {"prompt_tokens": 10, "completion_tokens": None, "total_tokens": None},
        ),
        (
            {"completion_tokens": 5, "total_tokens": 15},
            {"prompt_tokens": None, "completion_tokens": 5, "total_tokens": 15},
        ),
        (
            {"prompt_tokens": "10", "completion_tokens": "5", "total_tokens": "15"},
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
        ({"prompt_tokens": -1, "completion_tokens": 5, "total_tokens": 4}, None),
        ({"prompt_tokens": 1.5}, None),
        ({"prompt_tokens": "unknown"}, None),
        ("invalid", None),
    ],
)
def test_actual_model_and_optional_usage_are_preserved(raw_usage, expected):
    payload = completion()
    payload["model"] = "actual/returned-model"
    payload["usage"] = raw_usage

    async def run():
        async with open_chat_model(
            api_key=API_KEY,
            model=MODEL,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        ) as model:
            answer = await model.ainvoke("Hello")
        assert answer.content == "Use AsyncClient."
        assert answer.response_metadata["model_name"] == "actual/returned-model"
        assert answer.response_metadata["token_usage"] == expected
        if expected is None or any(value is None for value in expected.values()):
            assert answer.usage_metadata is None
        else:
            assert answer.usage_metadata["total_tokens"] == expected["total_tokens"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "reason", ["length", "content_filter", "error", "unknown", None]
)
def test_non_complete_answers_are_rejected_even_with_text(reason):
    payload = completion(content="An incomplete answer that must not be stored")
    payload["choices"][0]["finish_reason"] = reason

    async def run():
        with pytest.raises(ProviderError):
            async with open_chat_model(
                api_key=API_KEY,
                model=MODEL,
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, json=payload)
                ),
            ) as model:
                await model.ainvoke("Hello")

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["error_with_choices", "invalid_tool", "malformed_usage_and_choice"]
)
def test_http_200_errors_are_safe_before_callbacks(failure):
    detail = "private-upstream-detail-must-not-appear"
    payload = completion()
    if failure == "error_with_choices":
        payload["error"] = {"code": 503, "message": detail}
    elif failure == "invalid_tool":
        payload["choices"][0]["finish_reason"] = "tool_calls"
        payload["choices"][0]["message"]["tool_calls"] = [
            {
                "id": "call-fabricated",
                "type": "function",
                "function": {"name": "read_official_doc", "arguments": detail},
            }
        ]
    else:
        payload["usage"] = {"prompt_tokens": 10}
        payload["choices"] = detail
    completed, errors = [], []

    class Recorder(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            completed.append(response)

        def on_llm_error(self, error, **kwargs):
            errors.append(error)

    async def run():
        with pytest.raises(ProviderError) as caught:
            async with open_chat_model(
                api_key=API_KEY,
                model=MODEL,
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, json=payload)
                ),
            ) as model:
                await model.ainvoke("Hello", config={"callbacks": [Recorder()]})
        assert detail not in "".join(traceback.format_exception(caught.value))

    asyncio.run(run())
    assert completed == []
    assert len(errors) == 1
    assert isinstance(errors[0], ProviderError)
    assert detail not in str(errors[0])


def test_escaped_secret_in_ignored_response_field_is_rejected():
    payload = completion()
    payload["ignored_sdk_field"] = API_KEY
    escaped_key = "".join(f"\\u{ord(char):04x}" for char in API_KEY)
    encoded = json.dumps(payload).replace(API_KEY, escaped_key)

    async def run():
        with pytest.raises(ProviderError, match="invalid response") as caught:
            async with open_chat_model(
                api_key=API_KEY,
                model=MODEL,
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, text=encoded)
                ),
            ) as model:
                await model.ainvoke("Hello")
        assert API_KEY not in "".join(traceback.format_exception(caught.value))

    asyncio.run(run())


@pytest.mark.parametrize(
    "limits",
    [
        {"max_tokens": 0},
        {"max_tokens": True},
        {"max_tokens": 1.5},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": True},
        {"reasoning_effort": "unknown"},
    ],
)
def test_invalid_request_limits_never_reach_transport(limits):
    async def run():
        with pytest.raises(ProviderError, match="Invalid OpenRouter"):
            async with open_chat_model(
                api_key=API_KEY,
                model=MODEL,
                transport=httpx.MockTransport(
                    lambda _: pytest.fail("No request expected")
                ),
                **limits,
            ):
                pytest.fail("Invalid limits must not yield a model")

    asyncio.run(run())
