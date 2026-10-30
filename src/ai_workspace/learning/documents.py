import hashlib
import re
from datetime import UTC, datetime

import httpx2
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.http import fetch_source_bytes
from ai_workspace.learning.models import DocumentSection, OfficialDocument

SECTIONS: tuple[DocumentSection, ...] = (
    DocumentSection(
        id="testing",
        title="Testing",
        url="https://fastapi.tiangolo.com/tutorial/testing/",
    ),
    DocumentSection(
        id="async-tests",
        title="Async Tests",
        url="https://fastapi.tiangolo.com/advanced/async-tests/",
    ),
    DocumentSection(
        id="testing-dependencies",
        title="Testing Dependencies with Overrides",
        url="https://fastapi.tiangolo.com/advanced/testing-dependencies/",
    ),
)

_REMOVED_ELEMENTS = (
    "nav, aside, header, footer, script, style, noscript, template, button, svg, "
    "[role='navigation'], .md-nav, .headerlink, .md-clipboard, .copy-button, "
    ".copybtn, [data-copy-button], .linenodiv, .linenos, .lineno, .line-number"
)


def _code_language(pre: Tag) -> str | None:
    """只采用页面明确标记的代码语言，不根据代码内容猜测。"""
    for element in (pre, pre.find("code"), pre.parent):
        if not isinstance(element, Tag):
            continue
        for class_name in element.get("class", []):
            match = re.fullmatch(r"(?:language|lang)-([A-Za-z0-9_+-]+)", class_name)
            if match:
                return match.group(1)
    return None


def _extract_markdown(payload: bytes) -> str:
    """仅提取文章正文；站点导航或错误页不能作为学习材料。"""
    soup = BeautifulSoup(payload, "html.parser", from_encoding="utf-8")
    article = soup.select_one("article.md-content__inner") or soup.select_one(
        "main article"
    )
    if article is None:
        raise LearningSourceError("invalid_source_response")
    for element in article.select(_REMOVED_ELEMENTS):
        if element.parent is not None:
            element.decompose()
    if not article.get_text(strip=True):
        raise LearningSourceError("invalid_source_response")

    content = markdownify(
        str(article),
        heading_style="ATX",
        code_language_callback=_code_language,
    ).strip()
    if not content:
        raise LearningSourceError("invalid_source_response")
    return content


class OfficialDocumentReader:
    """读取固定章节目录，不接受来自用户或模型的任意网址。"""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def read(self, section_id: str) -> OfficialDocument:
        section = next((item for item in SECTIONS if item.id == section_id), None)
        if section is None:
            raise LearningSourceError("section_not_found", status_code=404)
        payload = await fetch_source_bytes(
            self.client,
            section.url,
            content_types=("text/html",),
            max_bytes=1_500_000,
        )
        content = _extract_markdown(payload)
        return OfficialDocument(
            section_id=section.id,
            title=section.title,
            url=section.url,
            content_markdown=content,
            fetched_at=datetime.now(UTC),
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
