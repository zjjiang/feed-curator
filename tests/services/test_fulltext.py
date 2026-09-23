import socket

import httpx
import pytest

from app.services import fulltext
from app.services.fulltext import ArchiveError, assert_public_url, fetch_html, normalize_input_url, parse_page


HTML_MAIN = """
<html><head>
  <meta property="og:image" content="/img/cover.jpg">
</head><body>
  <nav>导航</nav>
  <script>evil()</script>
  <main><h1>主内容标题</h1><p>这是正文段落,足够长。</p></main>
  <footer>页脚</footer>
</body></html>
"""

HTML_ARTICLE_META = """
<html><head><title>旧标题</title>
  <meta property="og:title" content="OG 标题">
  <meta property="og:description" content="OG 摘要">
  <meta name="author" content="张三">
</head><body>
  <article><p>文章容器正文。</p><form>订阅表单</form></article>
</body></html>
"""

HTML_TITLE_FALLBACK = "<html><head><title>仅标题</title></head><body><p>内容</p></body></html>"


class TestParsePage:
    def test_main_container_and_noise_stripped(self):
        r = parse_page(HTML_MAIN, "https://example.com/post")
        assert "主内容标题" in r["content_text"]
        assert "正文段落" in r["content_text"]
        assert "导航" not in r["content_text"]
        assert "页脚" not in r["content_text"]
        assert "evil" not in r["content_html"]
        assert r["cover_image_url"] == "https://example.com/img/cover.jpg"

    def test_article_container_and_meta(self):
        r = parse_page(HTML_ARTICLE_META, "https://example.com/a")
        assert r["title"] == "OG 标题"
        assert r["description"] == "OG 摘要"
        assert r["author"] == "张三"
        assert "文章容器正文" in r["content_text"]
        assert "订阅表单" not in r["content_html"]

    def test_title_falls_back_to_title_tag(self):
        r = parse_page(HTML_TITLE_FALLBACK, "https://example.com/t")
        assert r["title"] == "仅标题"


class TestNormalizeInputUrl:
    def test_ok_and_normalized(self):
        assert normalize_input_url(" HTTPS://Example.com/a ") == "https://example.com/a"

    def test_rejects_missing_scheme(self):
        with pytest.raises(ArchiveError):
            normalize_input_url("example.com/a")

    def test_rejects_missing_host(self):
        with pytest.raises(ArchiveError):
            normalize_input_url("mailto:someone@example.com")

    def test_rejects_credentials(self):
        with pytest.raises(ArchiveError):
            normalize_input_url("https://user:pass@example.com/a")


def _fake_getaddrinfo(addresses):
    def fake(host, *a, **kw):
        return [(socket.AF_INET, 1, 6, "", (addr, 0)) for addr in addresses]
    return fake


class TestAssertPublicUrl:
    def test_rejects_localhost(self):
        with pytest.raises(ArchiveError, match="本机"):
            assert_public_url("http://localhost:9001/x")

    def test_rejects_missing_host(self):
        with pytest.raises(ArchiveError, match="主机名"):
            assert_public_url("file:///etc/passwd")

    def test_rejects_unresolvable(self, monkeypatch):
        def boom(host, *a, **kw):
            raise socket.gaierror("nope")
        monkeypatch.setattr(socket, "getaddrinfo", boom)
        with pytest.raises(ArchiveError, match="无法解析域名"):
            assert_public_url("http://no-such-host.invalid/x")

    def test_rejects_private_ip(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["192.168.1.10"]))
        with pytest.raises(ArchiveError, match="内网"):
            assert_public_url("http://intranet.example.com/x")

    def test_allows_public_ip(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo",
                            _fake_getaddrinfo(["93.184.216.34"]))
        assert_public_url("https://example.com/x") is None


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


class TestFetchHtml:
    def test_rejects_non_html_content_type(self):
        def handler(request):
            return httpx.Response(200, content=b"%PDF-1.4",
                                  headers={"content-type": "application/pdf"})
        with pytest.raises(ArchiveError, match="暂只支持网页"):
            fetch_html("https://example.com/file.pdf", transport=_transport(handler))

    def test_follows_redirect_with_recheck(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))

        def handler(request):
            if request.url.host == "redirector.example.com":
                return httpx.Response(302, headers={"location": "https://final.example.com/ok"})
            return httpx.Response(200, content=b"<html><body>final</body></html>",
                                  headers={"content-type": "text/html; charset=utf-8"})
        html, final_url = fetch_html("https://redirector.example.com/go",
                                     transport=_transport(handler))
        assert final_url == "https://final.example.com/ok"
        assert "final" in html

    def test_redirect_to_internal_host_rejected(self):
        def handler(request):
            return httpx.Response(302, headers={"location": "http://192.168.1.1/admin"})
        with pytest.raises(ArchiveError):
            fetch_html("https://evil.example.com/go", transport=_transport(handler))

    def test_http_error_wrapped(self):
        def handler(request):
            return httpx.Response(500)
        with pytest.raises(ArchiveError, match="网页抓取失败"):
            fetch_html("https://example.com/err", transport=_transport(handler))
