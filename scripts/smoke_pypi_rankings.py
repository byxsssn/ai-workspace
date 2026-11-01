"""真实排行榜检查；默认匿名只读，--with-agent 会创建测试会话并产生模型费用。"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--with-agent", action="store_true")
    parser.add_argument("--env-file", default=".env")
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
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if not local and parsed.scheme != "https":
        parser.error("远程检查必须使用 HTTPS")
    if (
        args.with_agent
        and not local
        and Path(args.env_file).resolve() == Path(".env").resolve()
    ):
        parser.error("远程模型验证必须通过 --env-file 指定独立测试配置")

    try:
        with httpx.Client(
            base_url=origin + "/api/",
            timeout=150,
            follow_redirects=False,
            trust_env=False,
        ) as client:

            def request(path, *, method="GET", body=None, params=None, expected=200):
                response = client.request(method, path, json=body, params=params)
                if response.status_code != expected:
                    raise RuntimeError(
                        f"{method} {path}：预期 {expected}，实际 {response.status_code}"
                    )
                return response.json()

            first = request("rankings/pypi")
            repeated = request("rankings/pypi")
            assert first["snapshot_id"] == repeated["snapshot_id"]
            assert first["fetched_at"] == repeated["fetched_at"]
            assert first["source"] == "hugovk/top-pypi-packages"
            assert first["candidate_count"] == 20
            assert len(first["items"]) == first["filtered_count"] == 20
            assert not {"user_id", "email", "notes_markdown"} & first.keys()
            present = [i for i in first["items"] if i["download_count"] is not None]
            assert present == sorted(
                present, key=lambda item: (-item["download_count"], item["project"])
            )
            for item in first["items"]:
                if item["download_count"] is None:
                    assert item["rank"] is None
            filtered = request("rankings/pypi", params={"category": "web"})
            assert filtered["items"]
            assert all(item["category"] == "web" for item in filtered["items"])
            selected = request(
                "rankings/pypi", params=[("projects", "fastapi"), ("projects", "flask")]
            )
            assert {item["project"] for item in selected["items"]} == {
                "fastapi",
                "flask",
            }
            empty = request("rankings/pypi", params={"query": "no-such-smoke-package"})
            assert empty["items"] == [] and empty["filtered_count"] == 0
            request("conversations", expected=401)
            request("learning/records", expected=401)
            print(
                "通过：匿名榜单、缓存复用、分类/选择/搜索、缺失值与私人接口隔离",
                flush=True,
            )
            print(
                json.dumps(
                    {
                        "period": first["period"],
                        "source_count": first["source_count"],
                        "matched_count": first["matched_count"],
                        "stale": first["stale"],
                        "snapshot_id": first["snapshot_id"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if not args.with_agent:
                return 0

            config = dotenv_values(args.env_file)
            email = config.get("AI_WORKSPACE_TEST_EMAIL")
            password = config.get("AI_WORKSPACE_TEST_PASSWORD")
            model = config.get("AI_WORKSPACE_TEST_MODEL")
            if not all((email, password, model)):
                raise RuntimeError("所选配置文件缺少测试邮箱、密码或模型")
            token = request(
                "auth/login", method="POST", body={"email": email, "password": password}
            )["access_token"]
            client.headers["Authorization"] = f"Bearer {token}"
            request("providers/openrouter")
            conversation = request(
                "conversations",
                method="POST",
                body={"title": "PyPI 排行榜真实联调"},
                expected=201,
            )
            prompt = (
                "请调用网站的 query_pypi_rankings 工具，查询 fastapi 和 flask。"
                "逐项列出 project 和原始 download_count 整数，不要缩写为万或百万，"
                "并说明统计月份。若未收录就明确说明，不估算，不比较增长率。"
            )
            path = f"conversations/{conversation['id']}"
            reply = request(
                path + "/chat", method="POST", body={"model": model, "content": prompt}
            )
            content = reply["content"]
            assert selected["snapshot_id"][:12] in content
            assert selected["period"] in content
            assert selected["source_url"] in content
            for item in selected["items"]:
                assert item["project"] in content.lower()
                if item["download_count"] is not None:
                    assert str(item["download_count"]) in content.replace(",", "")
            messages = request(path + "/messages")
            assert [message["role"] for message in messages] == ["user", "assistant"]
            assert [message["content"] for message in messages] == [prompt, content]
            assert token not in json.dumps(messages)
            print("通过：真实 Agent 工具读取、数字/来源核对、回答持久化", flush=True)
            print(
                json.dumps(
                    {"conversation_id": conversation["id"], "model": reply["model"]}
                )
            )
        return 0
    except (
        httpx.HTTPError,
        ValueError,
        KeyError,
        AssertionError,
        RuntimeError,
    ) as error:
        # 不输出请求、配置或响应原文，避免模型与服务异常携带凭据。
        print(
            "失败：",
            str(error) if isinstance(error, RuntimeError) else type(error).__name__,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
