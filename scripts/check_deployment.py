"""Read-only checks against a deployed production build (no model calls)."""

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
    parser.add_argument("url", help="Production origin, without /api")
    parser.add_argument("--wait", type=int, default=60)
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
        parser.error("Expected an origin without credentials, path, query, or fragment")
    if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        parser.error("Remote checks require HTTPS")
    opener = build_opener(NoRedirect())

    def get(path: str, expected: int):
        try:
            response = opener.open(Request(origin + path), timeout=5)
        except HTTPError as error:
            response = error
        with response:
            if response.code != expected:
                raise RuntimeError(f"{path}: expected {expected}, got {response.code}")
            return response.read().decode(), response.headers

    deadline = time.monotonic() + args.wait
    while True:
        try:
            get("/readyz", 200)
            break
        except RuntimeError, URLError, TimeoutError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Production readiness check failed") from None
            time.sleep(1)
    html, headers = get("/", 200)
    assert "AI Workspace" in html and "text/html" in headers["content-type"]
    assert headers["cache-control"] == "no-store"
    assets = re.findall(r'(?:src|href)="(/assets/[^"<>]+)"', html)
    assert assets, "No production assets found"
    for asset in assets:
        _, asset_headers = get(asset, 200)
        assert "immutable" in asset_headers["cache-control"]
    get("/api/health", 200)
    get("/api/users/me", 401)
    for path in ("/api/not-found", "/api/docs", "/api/openapi.json", "/.env"):
        body, _ = get(path, 404)
        assert "<html" not in body.lower()
    print(
        "PASS: readiness, frontend assets, API routing, authentication, and production isolation"
    )


if __name__ == "__main__":
    main()
