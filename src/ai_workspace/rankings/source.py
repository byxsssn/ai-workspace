"""固定上游的有界下载和严格校验，不接受用户提供的 URL。"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from ai_workspace.rankings.catalog import normalize_project
from ai_workspace.rankings.errors import RankingUnavailable

SOURCE_URL = "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json"
MAX_SOURCE_BYTES = 2_000_000


def previous_month(now: datetime) -> str:
    return (now.date().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


@dataclass(frozen=True)
class ParsedSnapshot:
    id: str
    source_updated_at: datetime
    period: str
    payload: dict


def parse_snapshot(payload: bytes, *, now: datetime) -> ParsedSnapshot:
    try:
        if not payload or len(payload) > MAX_SOURCE_BYTES:
            raise ValueError
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("source") != "ClickHouse":
            raise ValueError
        if data.get("exception") or data.get("error"):
            raise ValueError
        updated = datetime.strptime(data["last_update"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=UTC
        )
        if updated > now + timedelta(minutes=5):
            raise ValueError
        rows = data["rows"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 15_000:
            raise ValueError
        if "total_rows" in data and (
            type(data["total_rows"]) is not int or data["total_rows"] != len(rows)
        ):
            raise ValueError
        names: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise TypeError
            project = normalize_project(row["project"])
            downloads = row["download_count"]
            if (
                project in names
                or type(downloads) is not int
                or not 0 <= downloads <= 9_007_199_254_740_991
            ):
                raise ValueError
            names.add(project)
        return ParsedSnapshot(
            id=hashlib.sha256(payload).hexdigest(),
            source_updated_at=updated,
            period=previous_month(updated),
            payload=data,
        )
    except ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError:
        raise RankingUnavailable() from None


async def fetch_snapshot(*, now: datetime) -> ParsedSnapshot:
    try:
        async with asyncio.timeout(25):
            async with httpx.AsyncClient(
                timeout=15,
                trust_env=False,
                follow_redirects=False,
                headers={"User-Agent": "AI-Workspace-PyPI/0.1"},
            ) as client:
                async with client.stream("GET", SOURCE_URL) as response:
                    if response.status_code != 200:
                        raise RankingUnavailable()
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";")[0]
                        .strip()
                        .lower()
                    )
                    if content_type not in {"application/json", "text/plain"}:
                        raise RankingUnavailable()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_SOURCE_BYTES:
                            raise RankingUnavailable()
                        body.extend(chunk)
        return parse_snapshot(bytes(body), now=now)
    except httpx.HTTPError, TimeoutError, ValueError:
        raise RankingUnavailable() from None
