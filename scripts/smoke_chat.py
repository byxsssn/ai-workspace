"""Opt-in live smoke test through Vite. Uses .env, never prints credentials.

Run from the repository root: uv run python scripts/smoke_chat.py
Creates a clearly named conversation and makes two billable model requests.
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
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
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
        parser.error("Expected an origin without credentials, path, query or fragment")
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1") and (
        parsed.scheme != "https"
        or Path(args.env_file).resolve() == Path(".env").resolve()
    ):
        parser.error("Remote smoke tests require HTTPS and a separate --env-file")
    config = dotenv_values(args.env_file)
    email = config.get("AI_WORKSPACE_TEST_EMAIL")
    password = config.get("AI_WORKSPACE_TEST_PASSWORD")
    api_key = config.get("AI_WORKSPACE_TEST_OPENROUTER_API_KEY") or config.get(
        "OPENROUTER_TEST_API_KEY"
    )
    model = config.get("AI_WORKSPACE_TEST_MODEL")
    if not all((email, password, model)):
        print("Missing test email, password or model in the selected env file.")
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
            raise RuntimeError(f"{method} {path}: expected {expected}, got {status}")
        return json.loads(payload) if payload else None

    try:
        request("/health")
        token = request("/auth/login", "POST", {"email": email, "password": password})[
            "access_token"
        ]
        current_user = request("/users/me")
        # Reuse a configured test credential; only configure an absent one.
        try:
            credential = request("/providers/openrouter")
        except RuntimeError as error:
            if not str(error).endswith("got 404"):
                raise
            if not api_key:
                raise RuntimeError(
                    "Configure the test account's OpenRouter key in the UI first"
                ) from None
            credential = request("/providers/openrouter", "PUT", {"api_key": api_key})
        assert credential["configured"]
        assert "api_key" not in credential and "encrypted_api_key" not in credential
        print("PASS: proxy, login, user identity and credential metadata", flush=True)

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
        print("PASS: real model reply and two persisted messages", flush=True)

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
        print("PASS: multi-turn context, re-login and identical server history")
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
        # Do not dump response bodies, request objects or configuration values.
        print(
            "FAIL:",
            str(error) if isinstance(error, RuntimeError) else type(error).__name__,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
