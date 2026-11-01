import asyncio
import traceback

import httpx2
import pytest

from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.http import fetch_source_bytes


def fetch(handler, **kwargs):
    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler)
        ) as client:
            return await fetch_source_bytes(client, "https://example.com/doc", **kwargs)

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("status", "code", "api_status"),
    [
        (401, "source_unauthorized", 502),
        (403, "source_forbidden", 502),
        (404, "source_not_found", 502),
        (429, "source_rate_limited", 503),
        (500, "source_unavailable", 502),
    ],
)
def test_http_errors_do_not_expose_upstream_body(status, code, api_status):
    def handler(request):
        return httpx2.Response(status, text="private-token-and-private-notes")

    with pytest.raises(LearningSourceError) as exc_info:
        fetch(handler)
    assert exc_info.value.code == code
    assert exc_info.value.status_code == api_status
    assert "private-token" not in "".join(traceback.format_exception(exc_info.value))


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [("15", "15"), ("999999", "3600"), ("private", None), ("-1", None)],
)
def test_rate_limit_header_is_bounded(retry_after, expected):
    with pytest.raises(LearningSourceError) as exc_info:
        fetch(
            lambda request: httpx2.Response(429, headers={"Retry-After": retry_after})
        )
    assert exc_info.value.retry_after == expected


def test_transport_exception_does_not_leak_credentials():
    def handler(request):
        raise httpx2.ConnectError("private-credential", request=request)

    with pytest.raises(LearningSourceError) as exc_info:
        fetch(handler)
    assert exc_info.value.code == "source_unavailable"
    assert "private-credential" not in "".join(
        traceback.format_exception(exc_info.value)
    )


def test_timeout_is_distinguishable():
    def handler(request):
        raise httpx2.ReadTimeout("private-details", request=request)

    with pytest.raises(LearningSourceError) as exc_info:
        fetch(handler)
    assert exc_info.value.code == "source_timeout"
    assert exc_info.value.status_code == 504


def test_unexpected_content_type_is_rejected():
    with pytest.raises(LearningSourceError) as exc_info:
        fetch(lambda request: httpx2.Response(200, text="not json"))
    assert exc_info.value.code == "invalid_source_response"


def test_echoed_secret_is_rejected_even_in_success_response():
    with pytest.raises(LearningSourceError) as exc_info:
        fetch(
            lambda request: httpx2.Response(200, json={"content": "secret-token"}),
            sensitive_values=("secret-token",),
        )
    assert exc_info.value.code == "invalid_source_response"


def test_limited_bytes_are_measured_after_decoding():
    with pytest.raises(LearningSourceError) as exc_info:
        fetch(
            lambda request: httpx2.Response(200, json={"long": "x" * 50}), max_bytes=10
        )
    assert exc_info.value.code == "source_too_large"
