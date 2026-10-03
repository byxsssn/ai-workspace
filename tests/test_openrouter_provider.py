import asyncio
import json
import logging
import traceback

import httpx2
import pytest
from openai import DefaultAsyncHttpxClient

from ai_workspace.providers import (
    ModelMessage,
    ModelResponse,
    ProviderError,
    ProviderId,
    ReasoningEffort,
    TokenUsage,
    openrouter,
)
from ai_workspace.providers.openrouter import OpenRouterProvider

API_KEY = "sk-or-test-secret-must-not-appear"
UPSTREAM_DETAIL = "private-upstream-error-details"
ENDPOINT = "https://openrouter.ai/api/v1/responses"


def response_payload(content: str = "Hello") -> dict[str, object]:
    return {
        "id": API_KEY,
        "object": "response",
        "model": "returned-model",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "output": [
            {
                "id": "message-1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": content, "annotations": []}
                ],
            }
        ],
    }


def assert_safe_error(error: ProviderError, caplog: pytest.LogCaptureFixture) -> None:
    for sensitive_value in (API_KEY, UPSTREAM_DETAIL):
        assert sensitive_value not in str(error)
        assert sensitive_value not in repr(error)
        assert sensitive_value not in "".join(traceback.format_exception(error))
        assert sensitive_value not in caplog.text
    assert error.__cause__ is None


def complete(
    transport: httpx2.MockTransport,
    *,
    api_key: str = API_KEY,
    messages: list[ModelMessage] | None = None,
    reasoning_effort: ReasoningEffort | None = None,
) -> ModelResponse:
    clients: list[httpx2.AsyncClient] = []

    def create_http_client(**kwargs: object) -> httpx2.AsyncClient:
        client = DefaultAsyncHttpxClient(transport=transport, **kwargs)
        clients.append(client)
        return client

    async def run() -> ModelResponse:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(openrouter, "DefaultAsyncHttpxClient", create_http_client)
            try:
                return await OpenRouterProvider().generate(
                    api_key=api_key,
                    model="requested-model",
                    messages=messages or [ModelMessage(role="user", content="Hi")],
                    reasoning_effort=reasoning_effort,
                )
            finally:
                for client in clients:
                    assert client.is_closed
                    assert "Authorization" not in client.headers

    return asyncio.run(run())


def test_generate_sends_fixed_request_and_maps_response(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("OPENAI_API_KEY", "untrusted-environment-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://untrusted.example.test/")
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", "authorization: Bearer untrusted-environment-key"
    )
    requests: list[httpx2.Request] = []
    payload = response_payload()
    payload["usage"] = {
        "input_tokens": 12,
        "output_tokens": 3,
        "total_tokens": 15,
        "cost": 0.001,
    }

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=payload)

    messages = [
        ModelMessage(role="system", content="Answer helpfully"),
        ModelMessage(role="user", content="Earlier question"),
        ModelMessage(role="assistant", content="Earlier answer"),
        ModelMessage(role="user", content="Hi"),
    ]
    result = complete(httpx2.MockTransport(handle), messages=messages)

    assert isinstance(result, ModelResponse)
    assert OpenRouterProvider().provider_id is ProviderId.OPENROUTER
    assert result.content == "Hello"
    assert result.model == "returned-model"
    assert result.usage == TokenUsage(
        prompt_tokens=12, completion_tokens=3, total_tokens=15
    )
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == ENDPOINT
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.extensions["timeout"] == {
        "connect": 60.0,
        "read": 60.0,
        "write": 60.0,
        "pool": 60.0,
    }
    assert json.loads(request.content) == {
        "model": "requested-model",
        "input": [
            {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": "Answer helpfully"}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Earlier question"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "id": "msg_2",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Earlier answer",
                        "annotations": [],
                    }
                ],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Hi"}],
            },
        ],
        "store": False,
        "stream": False,
    }
    assert API_KEY not in repr(result)
    assert API_KEY not in caplog.text


@pytest.mark.parametrize(
    "reasoning_effort",
    [None, "none", "minimal", "low", "medium", "high", "xhigh", "max"],
)
def test_generate_maps_selected_reasoning_effort_to_responses_request(
    reasoning_effort: ReasoningEffort | None,
) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=response_payload())

    complete(httpx2.MockTransport(handle), reasoning_effort=reasoning_effort)

    assert len(requests) == 1
    assert str(requests[0].url) == ENDPOINT
    payload = json.loads(requests[0].content)
    assert "reasoning_effort" not in payload
    if reasoning_effort is None:
        assert "reasoning" not in payload
    else:
        assert payload["reasoning"] == {"effort": reasoning_effort}


@pytest.mark.parametrize("usage", [None, {}, {"output_tokens": 0}])
def test_generate_preserves_optional_usage(usage: dict[str, int] | None) -> None:
    payload = response_payload()
    if usage is not None:
        payload["usage"] = usage

    result = complete(
        httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    )

    if usage is None:
        assert result.usage is None
    else:
        assert result.usage == TokenUsage(completion_tokens=usage.get("output_tokens"))


@pytest.mark.parametrize(
    "usage",
    [
        "invalid",
        [],
        12,
        {"input_tokens": API_KEY},
        {"input_tokens": -1},
        {"input_tokens": 1.5},
        {"input_tokens": []},
        {"input_tokens": {}},
        {"input_tokens": 12, "output_tokens": -1, "total_tokens": 11},
    ],
)
def test_invalid_usage_does_not_discard_a_valid_answer(usage: object) -> None:
    payload = {**response_payload(), "usage": usage}

    result = complete(
        httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    )

    assert result.content == "Hello"
    assert result.usage is None
    assert API_KEY not in repr(result)


@pytest.mark.parametrize("input_tokens, expected", [("12", 12), (True, 1)])
def test_usage_accepts_sdk_normalization(input_tokens: object, expected: int) -> None:
    # A complete usage object is normalized by the SDK's typed model. The
    # adapter validates that result without re-reading the original JSON.
    payload = {
        **response_payload(),
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 3,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 15,
        },
    }

    result = complete(
        httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    )

    assert result.usage == TokenUsage(
        prompt_tokens=expected, completion_tokens=3, total_tokens=15
    )


def test_generate_aggregates_text_blocks_and_preserves_whitespace() -> None:
    content = "  Hello\n\nworld!  "
    payload = {
        **response_payload(),
        "output": [
            {
                "id": f"message-{index}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": text, "annotations": []}
                    for text in texts
                ],
            }
            for index, texts in enumerate([["  Hello", "\n\n"], ["world!  "]])
        ],
    }

    result = complete(
        httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    )

    assert result.content == content


@pytest.mark.parametrize("status_code", [401, 408, 429, 500, 503, 307])
def test_http_failures_are_safe_without_retries_or_redirects(
    status_code: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            status_code,
            json={"error": {"message": f"{UPSTREAM_DETAIL}: {API_KEY}"}},
            headers={"Location": "https://untrusted.example.test/"},
        )

    with pytest.raises(ProviderError) as exc_info:
        complete(httpx2.MockTransport(handle))

    assert exc_info.value.status_code == status_code
    assert str(exc_info.value) == f"OpenRouter returned HTTP {status_code}"
    assert len(requests) == 1
    assert str(requests[0].url) == ENDPOINT
    assert_safe_error(exc_info.value, caplog)


@pytest.mark.parametrize("error_type", [httpx2.ReadTimeout, httpx2.ConnectError])
def test_transport_failures_are_wrapped_without_sensitive_details(
    error_type: type[httpx2.RequestError], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        raise error_type(f"{UPSTREAM_DETAIL}: {API_KEY}", request=request)

    with pytest.raises(ProviderError) as exc_info:
        complete(httpx2.MockTransport(handle))

    assert exc_info.value.status_code is None
    assert str(exc_info.value) == "OpenRouter request failed"
    assert len(requests) == 1
    assert_safe_error(exc_info.value, caplog)


@pytest.mark.parametrize(
    "payload",
    [
        f"not JSON: {API_KEY}",
        [],
        None,
        {},
        {"error": {"message": API_KEY}},
        {**response_payload(), "output": []},
        {**response_payload(), "output": None},
        {**response_payload(), "output": [None]},
        {**response_payload(), "output": [{}]},
        {**response_payload(), "output": [{"type": "message", "content": None}]},
        {
            **response_payload(),
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": []}],
                }
            ],
        },
        {
            **response_payload(),
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "refusal", "refusal": API_KEY}],
                }
            ],
        },
        response_payload(""),
        {**response_payload(), "model": None},
        {**response_payload(), "model": ""},
        {**response_payload(), "model": []},
        {**response_payload(), "model": API_KEY},
        {**response_payload(), "status": "failed", "error": {"message": API_KEY}},
        {**response_payload(), "status": "incomplete"},
        {**response_payload(), "status": "in_progress"},
        {**response_payload(), "status": None},
        {**response_payload(), "error": {"message": API_KEY}},
        {
            **response_payload(),
            "incomplete_details": {"reason": "max_output_tokens"},
        },
        response_payload(f"Reflected key: {API_KEY}"),
    ],
)
def test_invalid_upstream_responses_raise_safe_provider_errors(
    payload: object, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def handle(_: httpx2.Request) -> httpx2.Response:
        if isinstance(payload, str):
            return httpx2.Response(200, content=payload)
        # Explicit JSON handles null as well as SDK-invalid field shapes.
        return httpx2.Response(
            200,
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

    with pytest.raises(ProviderError) as exc_info:
        complete(httpx2.MockTransport(handle))

    assert exc_info.value.status_code is None
    assert str(exc_info.value) == "OpenRouter returned an invalid response"
    assert_safe_error(exc_info.value, caplog)


def test_malformed_json_body_raises_safe_provider_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(ProviderError) as exc_info:
        complete(
            httpx2.MockTransport(
                lambda _: httpx2.Response(
                    200,
                    content=f'{{"private": "{API_KEY}"',
                    headers={"Content-Type": "application/json"},
                )
            )
        )

    assert str(exc_info.value) == "OpenRouter returned an invalid response"
    assert_safe_error(exc_info.value, caplog)


@pytest.mark.parametrize("api_key", ["", f"{API_KEY}\r\n", f"{API_KEY} 空格"])
def test_invalid_api_key_is_rejected_before_client_creation(
    api_key: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_client(**_: object) -> None:
        pytest.fail("Invalid API key must not create an SDK client")

    monkeypatch.setattr(openrouter, "AsyncOpenAI", unexpected_client)

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(
            OpenRouterProvider().generate(
                api_key=api_key,
                model="requested-model",
                messages=[ModelMessage(role="user", content="Hi")],
            )
        )

    assert exc_info.value.status_code is None
    assert_safe_error(exc_info.value, caplog)


def test_concurrent_requests_keep_credentials_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[httpx2.AsyncClient] = []
    requests: list[httpx2.Request] = []

    async def run() -> None:
        both_started = asyncio.Event()

        async def handle(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            if len(requests) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=5)
            payload = response_payload()
            payload["model"] = json.loads(request.content)["model"]
            return httpx2.Response(200, json=payload)

        def create_http_client(**kwargs: object) -> httpx2.AsyncClient:
            client = DefaultAsyncHttpxClient(
                transport=httpx2.MockTransport(handle), **kwargs
            )
            clients.append(client)
            return client

        monkeypatch.setattr(openrouter, "DefaultAsyncHttpxClient", create_http_client)
        provider = OpenRouterProvider()
        results = await asyncio.gather(
            *(
                provider.generate(
                    api_key=f"{API_KEY}-{index}",
                    model=f"requested-model-{index}",
                    messages=[ModelMessage(role="user", content="Hi")],
                )
                for index in range(2)
            )
        )
        assert [result.model for result in results] == [
            "requested-model-0",
            "requested-model-1",
        ]

    asyncio.run(run())

    assert len(clients) == len(requests) == 2
    for client in clients:
        assert client.is_closed
        assert "Authorization" not in client.headers
    for request in requests:
        payload = json.loads(request.content)
        index = payload["model"].rsplit("-", 1)[1]
        assert "reasoning" not in payload
        assert request.headers["Authorization"] == f"Bearer {API_KEY}-{index}"
        assert str(request.url) == ENDPOINT


def test_cancellation_propagates_and_closes_request_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[httpx2.AsyncClient] = []

    async def run() -> None:
        started = asyncio.Event()

        async def handle(_: httpx2.Request) -> httpx2.Response:
            started.set()
            await asyncio.Event().wait()
            pytest.fail("Cancelled request must not complete")

        def create_http_client(**kwargs: object) -> httpx2.AsyncClient:
            client = DefaultAsyncHttpxClient(
                transport=httpx2.MockTransport(handle), **kwargs
            )
            clients.append(client)
            return client

        monkeypatch.setattr(openrouter, "DefaultAsyncHttpxClient", create_http_client)
        task = asyncio.create_task(
            OpenRouterProvider().generate(
                api_key=API_KEY,
                model="requested-model",
                messages=[ModelMessage(role="user", content="Hi")],
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert len(clients) == 1
    assert clients[0].is_closed
