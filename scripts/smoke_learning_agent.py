"""本地学习 Agent 联调：登录测试账号，创建会话，验证两轮工具调用与消息保存。

读取 .env 中已有 AI_WORKSPACE_TEST_EMAIL / PASSWORD / MODEL。
复用账号已保存的 OpenRouter 凭据，不输出密钥或 JWT。
默认使用虚构学习进度与真实官方文档；会产生模型费用并保留测试会话。
"""

import argparse
import asyncio
import json
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2
from dotenv import dotenv_values


class SmokeFailure(Exception):
    pass


async def check(args: argparse.Namespace) -> dict[str, object]:
    config = dotenv_values(args.env_file)
    email = config.get("AI_WORKSPACE_TEST_EMAIL")
    password = config.get("AI_WORKSPACE_TEST_PASSWORD")
    model = config.get("AI_WORKSPACE_TEST_MODEL")
    if not all((email, password, model)):
        raise SmokeFailure("Missing local test email, password or model")

    async with httpx2.AsyncClient(
        base_url=args.base_url,
        timeout=150,
        follow_redirects=False,
        headers={"Cache-Control": "no-store"},
    ) as client:

        async def request(method: str, path: str, *, body=None, expected=200):
            response = await client.request(method, path, json=body)
            if response.status_code != expected:
                # 不打印原始响应、请求头或测试账号内容。
                code = None
                try:
                    detail = response.json().get("detail")
                    if isinstance(detail, dict):
                        candidate = detail.get("code", "")
                        if (
                            isinstance(candidate, str)
                            and candidate.replace("_", "").isalnum()
                        ):
                            code = candidate
                except ValueError, AttributeError:
                    pass
                raise SmokeFailure(
                    f"HTTP {response.status_code}: {code or 'request_failed'}"
                )
            return response.json()

        await request("GET", "/health")
        login = await request(
            "POST", "/auth/login", body={"email": email, "password": password}
        )
        client.headers["Authorization"] = f"Bearer {login['access_token']}"
        await request("GET", "/providers/openrouter")
        conversation = await request(
            "POST",
            "/conversations",
            body={"title": f"学习 Agent 联调 {uuid4().hex[:8]}"},
            expected=201,
        )
        conversation_id = conversation["id"]
        questions = [
            (
                "根据演示进度继续学习 FastAPI Async Tests。先读取学习记录和官方异步测试章节，"
                "简短解释 AsyncClient 和 ASGITransport 的作用，并给一道小练习。"
            ),
            (
                "继续上一个问题：用一句话回顾刚才的小练习，再根据同一官方章节给一个提示；"
                "不要说你运行过代码。"
            ),
        ]
        turns = []
        for question in questions[: args.turns]:
            result = await request(
                "POST",
                f"/learning/fastapi/conversations/{conversation_id}/chat",
                body={"model": model, "content": question, "source": "demo"},
            )
            successful = {
                item["tool"]
                for item in result["tool_calls"]
                if item["status"] == "success"
            }
            if result["status"] != "answered" or successful != {
                "get_learning_state",
                "read_official_doc",
            }:
                summary = {
                    "conversation_id": conversation_id,
                    "status": result["status"],
                    "tool_calls": result["tool_calls"],
                }
                raise SmokeFailure(
                    "Agent did not complete both source-reading tools: "
                    + json.dumps(summary, ensure_ascii=False)
                )
            if not any(s["source_id"] == "doc:async-tests" for s in result["sources"]):
                raise SmokeFailure("Agent did not cite the requested official chapter")
            if result["source"] != "demo" or "演示模式" not in result["content"]:
                raise SmokeFailure("Demo learning state must be explicitly labelled")
            turns.append(
                {
                    "status": result["status"],
                    "tools": result["tool_calls"],
                    "sources": result["sources"],
                    "usage": result["usage"],
                    "answer": result["content"],
                }
            )
        history = await request("GET", f"/conversations/{conversation_id}/messages")
        if [item["role"] for item in history] != ["user", "assistant"] * args.turns:
            raise SmokeFailure("Persisted message order did not match completed turns")
        if history[-1]["content"] != turns[-1]["answer"]:
            raise SmokeFailure("Persisted answer did not match the returned answer")
        return {
            "conversation_id": conversation_id,
            "persisted_messages": len(history),
            "turns": turns,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--turns", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        parser.error("Only a local backend URL without an /api prefix is supported")
    try:
        print(json.dumps(asyncio.run(check(args)), ensure_ascii=False, indent=2))
    except SmokeFailure as exc:
        print(str(exc))
        return 1
    except httpx2.HTTPError, ValueError, KeyError, TypeError:
        print("Learning smoke failed; check the local service and test configuration")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
