"""只读检查已部署的生产版本，不调用模型。"""

import argparse
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="生产服务地址，不包含 /api 路径")
    parser.add_argument(
        "--wait", type=int, default=60, help="等待服务就绪的秒数，默认 60"
    )
    args = parser.parse_args()
    origin = args.url.rstrip("/")
    url = urlsplit(origin)
    if (
        url.scheme not in ("https", "http")
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path
    ):
        parser.error("请提供不含凭据、路径、查询参数或片段的服务地址")
    if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        parser.error("远程检查必须使用 HTTPS")
    opener = build_opener(NoRedirect())

    def get(path: str, expected: int):
        try:
            response = opener.open(Request(origin + path), timeout=5)
        except HTTPError as error:
            response = error
        with response:
            if response.code != expected:
                raise RuntimeError(
                    f"{path}：预期状态码 {expected}，实际为 {response.code}"
                )
            return response.read().decode(), response.headers

    deadline = time.monotonic() + args.wait
    while True:
        try:
            get("/readyz", 200)
            break
        except RuntimeError, URLError, TimeoutError:
            if time.monotonic() >= deadline:
                raise RuntimeError("生产服务就绪检查失败") from None
            time.sleep(1)
    html, headers = get("/", 200)
    assert "AI Workspace" in html and "text/html" in headers["content-type"]
    assert headers["cache-control"] == "no-store"
    assets = re.findall(r'(?:src|href)="(/assets/[^"<>]+)"', html)
    assert assets, "未找到生产静态资源"
    for asset in assets:
        _, asset_headers = get(asset, 200)
        assert "immutable" in asset_headers["cache-control"]
    get("/api/health", 200)
    get("/api/users/me", 401)
    for path in ("/api/not-found", "/api/docs", "/api/openapi.json", "/.env"):
        body, _ = get(path, 404)
        assert "<html" not in body.lower()
    print("通过：服务就绪、前端静态资源、API 路由、身份验证与生产环境隔离检查")


if __name__ == "__main__":
    main()
