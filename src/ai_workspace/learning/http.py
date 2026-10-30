import asyncio
import logging

import httpx2

from ai_workspace.learning.errors import LearningSourceError


def create_source_client() -> httpx2.AsyncClient:
    """独立客户端不携带模型密钥；敏感请求的详细日志保持关闭。"""
    for name in ("httpx2", "httpcore2"):
        loggers = [logging.getLogger(name)]
        while loggers:
            logger = loggers.pop()
            logger.setLevel(logging.WARNING)
            loggers.extend(logger.getChildren())
    return httpx2.AsyncClient(
        timeout=15.0,
        follow_redirects=False,
        headers={"User-Agent": "AI-Workspace-Learning/0.1 (documentation reader)"},
    )


async def fetch_source_bytes(
    client: httpx2.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    content_types: tuple[str, ...] = ("application/json",),
    max_bytes: int = 1_500_000,
    sensitive_values: tuple[str, ...] = (),
) -> bytes:
    """限制总时间和解压后的大小，不向调用者透传上游错误正文。"""
    try:
        async with asyncio.timeout(20):
            async with client.stream(
                "GET", url, headers=headers, follow_redirects=False
            ) as response:
                status_code = response.status_code
                if status_code == 429:
                    # 仅接收合理的秒数，不透传任意响应头内容。
                    value = response.headers.get("retry-after", "")
                    retry_after = (
                        str(min(int(value), 3600))
                        if value.isascii() and value.isdecimal() and len(value) <= 6
                        else None
                    )
                    raise LearningSourceError(
                        "source_rate_limited", status_code=503, retry_after=retry_after
                    )
                if status_code in (401, 403, 404):
                    code = {
                        401: "source_unauthorized",
                        403: "source_forbidden",
                        404: "source_not_found",
                    }[status_code]
                    raise LearningSourceError(code)
                if 300 <= status_code < 400:
                    raise LearningSourceError("source_redirect")
                if status_code != 200:
                    raise LearningSourceError("source_unavailable")
                content_type = response.headers.get("content-type", "").split(";")[0]
                if content_type.strip().lower() not in content_types:
                    raise LearningSourceError("invalid_source_response")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > max_bytes:
                        raise LearningSourceError("source_too_large")
                    body.extend(chunk)
                payload = bytes(body)
                if any(
                    value and value.encode("utf-8") in payload
                    for value in sensitive_values
                ):
                    raise LearningSourceError("invalid_source_response")
                return payload
    except TimeoutError, httpx2.TimeoutException:
        raise LearningSourceError("source_timeout", status_code=504) from None
    except httpx2.HTTPError, UnicodeError, ValueError:
        raise LearningSourceError("source_unavailable") from None
