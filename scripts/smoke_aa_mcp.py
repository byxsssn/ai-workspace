"""验证真实 AA MCP；--with-agent 额外创建测试会话并产生一次 Agent 回合费用。"""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values


async def check_mcp(env_file: str) -> dict:
    from ai_workspace.agents.aa_client import AAClient
    from ai_workspace.agents.aa_config import AASettings

    settings = AASettings(_env_file=env_file)
    if not settings.enabled:
        raise RuntimeError("AA MCP 未启用，请先配置所选环境文件。")
    client = AAClient(settings)
    ranked = await client.call("query_model_rankings", {"metric": "value", "limit": 3})
    items = ranked["items"]
    if not items:
        raise RuntimeError("AA 快照没有满足条件的可比较配置。")
    meta = ranked["metadata"]
    ids = [item["id"] for item in items]
    compared = await client.call(
        "compare_models",
        {"model_ids": ids, "expected_snapshot_id": meta["snapshot_id"]},
    )
    assert compared["metadata"]["snapshot_id"] == meta["snapshot_id"]
    assert [item["id"] for item in compared["items"]] == ids
    assert not compared["unknown_model_ids"]
    for actual, expected in zip(compared["items"], items, strict=True):
        for field in ("intelligence_index", "evaluation_task_cost", "value_score"):
            assert actual[field] == expected[field]
    print("通过：真实 MCP 查询、同快照比较、结构化指标核对", flush=True)
    print(
        json.dumps(
            {
                "snapshot_id": meta["snapshot_id"],
                "index_version": meta["index_version"],
                "fetched_at_utc": meta["fetched_at_utc"],
                "total_configurations": meta["total_configurations"],
                "eligible_configurations": meta["eligible_configurations"],
                "compared_configurations": len(items),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return compared


def check_agent(args: argparse.Namespace, reference: dict) -> None:
    config = dotenv_values(args.env_file)
    email = config.get("AI_WORKSPACE_TEST_EMAIL")
    password = config.get("AI_WORKSPACE_TEST_PASSWORD")
    model = config.get("AI_WORKSPACE_TEST_MODEL")
    if not all((email, password, model)):
        raise RuntimeError("所选配置文件缺少测试邮箱、密码或模型。")

    with httpx.Client(
        base_url=args.base_url.rstrip("/") + args.api_prefix + "/",
        timeout=150,
        follow_redirects=False,
        trust_env=False,
    ) as client:

        def request(path, *, method="GET", body=None, expected=200):
            response = client.request(method, path, json=body)
            if response.status_code != expected:
                raise RuntimeError("测试 HTTP 请求未返回预期状态，请检查本地服务。")
            return response.json()

        token = request(
            "auth/login", method="POST", body={"email": email, "password": password}
        )["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        request("providers/openrouter")
        conversation = request(
            "conversations",
            method="POST",
            body={"title": "AA MCP 模型比较真实联调"},
            expected=201,
        )
        prompt = (
            "请先调用 query_model_rankings，metric=value、limit=3，"
            "再把返回的3个配置ID交给 compare_models 比较。"
            "对每个配置逐字列出 id、name，以及原始 intelligence_index、"
            "evaluation_task_cost、value_score 数字，不要四舍五入或改写单位。"
            "说明评测版本、快照标识和采集时间；采集时间缺失就说未知。"
            "解释这里的成本是评测任务成本，不是我的实际API账单。"
            "如果工具不可用，明确说明，不编造结果。"
        )
        path = f"conversations/{conversation['id']}"
        reply = request(
            path + "/chat", method="POST", body={"model": model, "content": prompt}
        )
        content = reply["content"]
        meta = reference["metadata"]
        assert meta["snapshot_id"][:12] in content
        assert meta["source_url"] in content
        compact = content.replace(",", "")
        for item in reference["items"]:
            assert item["id"] in content
            for field in ("intelligence_index", "evaluation_task_cost", "value_score"):
                number = item[field]
                assert number is not None
                assert str(number).removesuffix(".0") in compact
        messages = request(path + "/messages")
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert [message["content"] for message in messages] == [prompt, content]
        assert token not in json.dumps(messages)
        print("通过：真实 Agent 回答的指标/来源核对与会话持久化", flush=True)
        print(
            json.dumps(
                {"conversation_id": conversation["id"], "model": reply["model"]}
            ),
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--with-agent", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--api-prefix", choices=("", "/api"), default="/api")
    args = parser.parse_args()
    parsed = urlsplit(args.base_url.rstrip("/"))
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
        reference = asyncio.run(check_mcp(args.env_file))
        if args.with_agent:
            check_agent(args, reference)
        return 0
    except Exception:  # noqa: BLE001 - 统一隐藏 SDK、配置或 HTTP 异常中的私有正文。
        # SDK、配置校验与 HTTP 异常可能含本地路径或私有正文，不直接打印。
        print("验证失败：请检查 AA 配置、快照、服务和测试账号；未输出私有请求内容。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
