"""手动运行的真实聊天检查，默认经 Vite 访问本地服务；读取配置但不输出凭据。

在仓库根目录运行：uv run python scripts/smoke_chat.py
会创建名称明确的测试会话，并产生两次计费模型调用。
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from dotenv import dotenv_values


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", default=".env", help="测试账号配置文件；远程验证必须使用独立文件"
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:5173",
        help="前端服务地址，不包含 /api 路径",
    )
    args = parser.parse_args()
    origin = args.base_url.rstrip("/")
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        parser.error("请提供不含凭据、路径、查询参数或片段的服务地址")
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1") and (
        parsed.scheme != "https"
        or Path(args.env_file).resolve() == Path(".env").resolve()
    ):
        parser.error("远程验证必须使用 HTTPS，并通过 --env-file 指定独立配置文件")
    config = dotenv_values(args.env_file)
    email = config.get("AI_WORKSPACE_TEST_EMAIL")
    password = config.get("AI_WORKSPACE_TEST_PASSWORD")
    api_key = config.get("AI_WORKSPACE_TEST_OPENROUTER_API_KEY") or config.get(
        "OPENROUTER_TEST_API_KEY"
    )
    model = config.get("AI_WORKSPACE_TEST_MODEL")
    if not all((email, password, model)):
        print("所选配置文件缺少测试邮箱、密码或模型。")
        return 1

    opener = build_opener(NoRedirect())
    token = None

    def request(path, method="GET", body=None, expected=200):
        headers = {"Content-Type": "application/json", "Cache-Control": "no-store"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = Request(
            origin + "/api" + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
            method=method,
        )
        try:
            with opener.open(req, timeout=120) as response:
                status = response.status
                payload = response.read()
        except HTTPError as error:
            status, payload = error.code, error.read()
        if status != expected:
            raise RuntimeError(
                f"{method} {path}：预期状态码 {expected}，实际为 {status}"
            )
        return json.loads(payload) if payload else None

    try:
        request("/health")
        token = request("/auth/login", "POST", {"email": email, "password": password})[
            "access_token"
        ]
        current_user = request("/users/me")
        # 复用已配置的测试凭据，仅在没有凭据时写入测试密钥。
        try:
            credential = request("/providers/openrouter")
        except RuntimeError as error:
            if not str(error).endswith("实际为 404"):
                raise
            if not api_key:
                raise RuntimeError(
                    "请先在页面中配置测试账号的 OpenRouter 密钥"
                ) from None
            credential = request("/providers/openrouter", "PUT", {"api_key": api_key})
        assert credential["configured"]
        assert "api_key" not in credential and "encrypted_api_key" not in credential
        print("通过：代理、登录、用户身份与凭据元信息检查", flush=True)

        marker = "ORBIT_" + uuid4().hex[:8]
        conversation = request(
            "/conversations",
            "POST",
            {"title": "前端联调验证 " + marker},
            expected=201,
        )
        path = f"/conversations/{conversation['id']}"
        assert request(path + "/messages") == []
        first_prompt = f"本轮测试记忆词是 {marker}。请只回复：连接成功。"
        first = request(
            path + "/chat", "POST", {"model": model, "content": first_prompt}
        )
        assert first["content"].strip()
        history = request(path + "/messages")
        assert [item["role"] for item in history] == ["user", "assistant"]
        assert [item["content"] for item in history] == [first_prompt, first["content"]]
        print("通过：真实模型回复与两条消息持久化检查", flush=True)

        second = request(
            path + "/chat",
            "POST",
            {"model": model, "content": "上一轮的测试记忆词是什么？请只返回记忆词。"},
        )
        assert marker in second["content"]
        before = request(path + "/messages")
        token = request("/auth/login", "POST", {"email": email, "password": password})[
            "access_token"
        ]
        assert request("/users/me")["id"] == current_user["id"]
        after = request(path + "/messages")
        assert before == after and len(after) == 4
        assert any(
            item["id"] == conversation["id"] for item in request("/conversations")
        )
        serialized = json.dumps(after)
        assert (not api_key or api_key not in serialized) and token not in serialized
        print("通过：多轮上下文、重新登录与服务端历史一致性检查")
        print(
            json.dumps(
                {
                    "conversation_id": conversation["id"],
                    "model": second["model"],
                    "messages": len(after),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (RuntimeError, AssertionError, URLError, TimeoutError, KeyError) as error:
        # 不输出响应体、请求对象或配置值。
        print(
            "失败：",
            str(error) if isinstance(error, RuntimeError) else type(error).__name__,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
