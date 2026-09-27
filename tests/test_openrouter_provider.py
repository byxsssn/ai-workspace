import asyncio
import json
import logging
import traceback

import httpx2
import pytest

from ai_workspace.providers import (
    ChatCompletionResponse,
    ChatMessage,
    OpenRouterProvider,
    ProviderError,
    TokenUsage,
)

API_KEY = "sk-or-test-secret-must-not-appear"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def completion_payload() -> dict[str, object]:
    return {
        "id": API_KEY,
        "model": "returned-model",
        "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
    }


def assert_safe_error(error: ProviderError, caplog: pytest.LogCaptureFixture) -> None:
    assert API_KEY not in str(error)
    assert API_KEY not in repr(error)
    assert API_KEY not in "".join(traceback.format_exception(error))
    assert API_KEY not in caplog.text
    assert error.__cause__ is None


def complete(
    transport: httpx2.MockTransport, *, api_key: str = API_KEY
) -> ChatCompletionResponse:
    async def run() -> ChatCompletionResponse:
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://untrusted.example.test/",
            follow_redirects=True,
            auth=httpx2.BasicAuth("client-default", "unused"),
        ) as client:
            result = await OpenRouterProvider(client).chat_completion(
                api_key=api_key,
                model="requested-model",
                messages=[ChatMessage(role="user", content="Hi")],
            )
            assert not client.is_closed
            assert "Authorization" not in client.headers
            return result

    return asyncio.run(run())


def test_chat_completion_sends_fixed_request_and_maps_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    requests: list[httpx2.Request] = []
    payload = completion_payload()
    payload["usage"] = {
        "prompt_tokens": 12,
        "completion_tokens": 3,
        "total_tokens": 15,
        "cost": 0.001,
    }

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=payload)

    result = complete(httpx2.MockTransport(handle))

    assert isinstance(result, ChatCompletionResponse)
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
    assert json.loads(request.content) == {
        "model": "requested-model",
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": False,
    }
    assert API_KEY not in repr(result)
    assert API_KEY not in caplog.text


@pytest.mark.parametrize("usage", [None, {"completion_tokens": 0}])
def test_chat_completion_preserves_optional_usage(usage: dict[str, int] | None) -> None:
    payload = completion_payload()
    if usage is not None:
        payload["usage"] = usage

    result = complete(
        httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    )

    if usage is None:
        assert result.usage is None
    else:
        assert result.usage == TokenUsage(completion_tokens=0)


@pytest.mark.parametrize("status_code", [401, 307])
def test_http_failures_are_safe_and_redirects_are_not_followed(
    status_code: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            status_code,
            json={"error": {"message": API_KEY}},
            headers={"Location": "https://untrusted.example.test/"},
        )

    with pytest.raises(ProviderError) as exc_info:
        complete(httpx2.MockTransport(handle))

    assert exc_info.value.status_code == status_code
    assert len(requests) == 1
    assert str(requests[0].url) == ENDPOINT
    assert_safe_error(exc_info.value, caplog)


@pytest.mark.parametrize("error_type", [httpx2.ReadTimeout, httpx2.ConnectError])
def test_transport_failures_are_wrapped_without_sensitive_details(
    error_type: type[httpx2.RequestError], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def handle(request: httpx2.Request) -> httpx2.Response:
        raise error_type(API_KEY, request=request)

    with pytest.raises(ProviderError) as exc_info:
        complete(httpx2.MockTransport(handle))

    assert exc_info.value.status_code is None
    assert_safe_error(exc_info.value, caplog)


@pytest.mark.parametrize(
    "payload",
    [
        f"not JSON: {API_KEY}",
        {"error": {"message": API_KEY}},
        {"model": "returned-model", "choices": []},
        {**completion_payload(), "usage": {"prompt_tokens": API_KEY}},
        {
            **completion_payload(),
            "choices": [{"message": {"content": f"Reflected key: {API_KEY}"}}],
        },
    ],
)
def test_invalid_upstream_responses_raise_safe_provider_errors(
    payload: dict[str, object] | str, caplog: pytest.LogCaptureFixture
) -> None:
    def handle(_: httpx2.Request) -> httpx2.Response:
        if isinstance(payload, str):
            return httpx2.Response(200, content=payload)
        return httpx2.Response(200, json=payload)

    with pytest.raises(ProviderError) as exc_info:
        complete(httpx2.MockTransport(handle))

    assert exc_info.value.status_code is None
    assert_safe_error(exc_info.value, caplog)


def test_invalid_api_key_is_rejected_before_transport(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handle(_: httpx2.Request) -> httpx2.Response:
        pytest.fail("Invalid API key must not reach HTTP transport")

    with pytest.raises(ProviderError) as exc_info:
        complete(httpx2.MockTransport(handle), api_key=f"{API_KEY}\r\n")

    assert exc_info.value.status_code is None
    assert_safe_error(exc_info.value, caplog)
