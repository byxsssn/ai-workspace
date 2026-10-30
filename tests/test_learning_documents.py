import asyncio
import hashlib
from datetime import UTC, datetime

import httpx2
import pytest

from ai_workspace.learning.documents import OfficialDocumentReader
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.models import OfficialDocument

ARTICLE = """<!doctype html>
<html><body>
<nav>Global navigation must not be learning content</nav>
<main><article class="md-content__inner md-typeset">
<h1>Testing<a class="headerlink" href="#testing">¶</a></h1>
<p>使用 TestClient 测试接口。</p>
<h2>Write a test</h2>
<div class="language-python highlight"><pre><code><span class="linenos">1</span><span>def test_answer():</span>
<span class="linenos">2</span><span>    assert {"text": "a_b * c"} == {"text": "a_b * c"}</span>
</code></pre></div>
<nav>Article navigation must not be learning content</nav>
<div role="navigation">Secondary navigation</div>
<button class="md-clipboard">Copy code</button>
<a class="copy-button" href="#copy">Copy example</a>
<script>console.log("hidden script")</script>
<style>.hidden-style { display: none; }</style>
</article></main><footer>Site footer</footer>
</body></html>"""


def read_document(
    transport: httpx2.MockTransport, section_id: str = "testing"
) -> OfficialDocument:
    async def run() -> OfficialDocument:
        # 即使调用者启用了重定向，读取器使用的共享请求边界也必须拒绝跳转。
        async with httpx2.AsyncClient(
            transport=transport, follow_redirects=True
        ) as client:
            return await OfficialDocumentReader(client).read(section_id)

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("section_id", "title", "url"),
    [
        ("testing", "Testing", "https://fastapi.tiangolo.com/tutorial/testing/"),
        (
            "async-tests",
            "Async Tests",
            "https://fastapi.tiangolo.com/advanced/async-tests/",
        ),
        (
            "testing-dependencies",
            "Testing Dependencies with Overrides",
            "https://fastapi.tiangolo.com/advanced/testing-dependencies/",
        ),
    ],
)
def test_reads_only_the_catalogued_section(
    section_id: str, title: str, url: str
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200, text=ARTICLE, headers={"content-type": "text/html; charset=utf-8"}
        )

    before = datetime.now(UTC)
    document = read_document(httpx2.MockTransport(respond), section_id)
    after = datetime.now(UTC)

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == url
    assert document.section_id == section_id
    assert document.title == title
    assert document.url == url
    assert before <= document.fetched_at <= after
    assert document.fetched_at.utcoffset().total_seconds() == 0
    assert document.version is None
    assert (
        document.content_sha256
        == hashlib.sha256(document.content_markdown.encode("utf-8")).hexdigest()
    )


def test_preserves_headings_code_and_indentation_without_page_chrome() -> None:
    document = read_document(
        httpx2.MockTransport(
            lambda request: httpx2.Response(
                200, text=ARTICLE, headers={"content-type": "text/html"}
            )
        )
    )

    content = document.content_markdown
    assert content.startswith("# Testing\n")
    assert "## Write a test" in content
    assert "使用 TestClient 测试接口。" in content
    assert (
        "```python\ndef test_answer():\n"
        '    assert {"text": "a_b * c"} == {"text": "a_b * c"}\n```'
    ) in content
    for excluded in (
        "Global navigation",
        "Article navigation",
        "Secondary navigation",
        "Copy code",
        "Copy example",
        "hidden script",
        "hidden-style",
        "Site footer",
        "¶",
    ):
        assert excluded not in content


def test_accepts_main_article_and_does_not_guess_code_language_or_version() -> None:
    html = """<main><article><h1>Async Tests</h1>
    <p>This example mentions Python 3.14 and FastAPI 0.141.</p>
    <pre><code>async def example():
    return True
</code></pre></article></main>"""
    document = read_document(
        httpx2.MockTransport(
            lambda request: httpx2.Response(
                200, text=html, headers={"content-type": "text/html"}
            )
        ),
        "async-tests",
    )

    assert "```\nasync def example():\n    return True\n```" in (
        document.content_markdown
    )
    assert document.version is None


@pytest.mark.parametrize(
    "section_id",
    ["unknown", "https://example.test/", "../testing", "testing?url=example.test"],
)
def test_unknown_section_is_rejected_without_a_request(section_id: str) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("An unknown section must not make an HTTP request")

    with pytest.raises(LearningSourceError) as caught:
        read_document(httpx2.MockTransport(respond), section_id)
    assert caught.value.code == "section_not_found"
    assert caught.value.status_code == 404


@pytest.mark.parametrize(
    "html",
    [
        "",
        "<main><nav><h1>Testing</h1><p>Navigation only</p></nav></main>",
        "<article><h1>Not a supported article container</h1></article>",
        '<article class="md-content__inner"> \n </article>',
        (
            '<article class="md-content__inner"><nav>Navigation only</nav>'
            "<script>hidden()</script><button>Copy</button></article>"
        ),
    ],
)
def test_rejects_missing_or_empty_article(html: str) -> None:
    with pytest.raises(LearningSourceError) as caught:
        read_document(
            httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200, text=html, headers={"content-type": "text/html"}
                )
            )
        )
    assert caught.value.code == "invalid_source_response"
    assert caught.value.status_code == 502


def test_rejects_oversized_html() -> None:
    with pytest.raises(LearningSourceError) as caught:
        read_document(
            httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200,
                    content=b" " * 1_500_001,
                    headers={"content-type": "text/html"},
                )
            )
        )
    assert caught.value.code == "source_too_large"


@pytest.mark.parametrize("status_code", [301, 302, 307, 308])
def test_rejects_redirect_without_requesting_the_destination(status_code: int) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            status_code, headers={"location": "https://untrusted.example.test/"}
        )

    with pytest.raises(LearningSourceError) as caught:
        read_document(httpx2.MockTransport(respond))
    assert caught.value.code == "source_redirect"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://fastapi.tiangolo.com/tutorial/testing/"


def test_rejects_non_html_source_response() -> None:
    with pytest.raises(LearningSourceError) as caught:
        read_document(
            httpx2.MockTransport(
                lambda request: httpx2.Response(200, json={"content": ARTICLE})
            )
        )
    assert caught.value.code == "invalid_source_response"
