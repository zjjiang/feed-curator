"""arxiv 适配器:关键词模式与 category 模式的请求形状 + Atom 解析。"""

from urllib.parse import parse_qs, urlsplit

import pytest

from app.adapters.arxiv import ArxivAdapter, build_keyword_query

ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2606.02578v1</id>
    <updated>2026-06-03T00:00:00Z</updated>
    <published>2026-06-02T17:59:59Z</published>
    <title> vision-language-action models survey </title>
    <summary>本篇综述讨论 VLA。第 1 行
第 2 行</summary>
    <author><name>张三</name></author>
    <author><name>Li Si</name></author>
    <link href="http://arxiv.org/abs/2606.02578v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2606.02578v1" type="application/pdf"/>
    <category term="cs.RO"/>
    <category term="cs.AI"/>
  </entry>
</feed>
"""


class _FakeResponse:
    def __init__(self, text):
        self.text = text


def _capture_request(monkeypatch, body=ATOM_XML):
    """把出网收口替换为记录 URL 的假实现,断言请求形状且不真发网络。"""
    captured = []

    def fake_request(url, **kwargs):
        captured.append(url)
        return _FakeResponse(body)

    monkeypatch.setattr("app.adapters.arxiv.outbound.request", fake_request)
    return captured


def _params(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


class TestKeywordMode:
    def test_request_shape(self, monkeypatch):
        captured = _capture_request(monkeypatch)
        items = ArxivAdapter().fetch({"query": build_keyword_query(
            ["humanoid robot", "vision-language-action"]), "max_results": 17})

        assert len(captured) == 1
        params = _params(captured[0])
        assert params["search_query"] == [
            'all:"humanoid robot" OR all:"vision-language-action"']
        assert params["sortBy"] == ["submittedDate"]
        assert params["sortOrder"] == ["descending"]
        assert params["max_results"] == ["17"]

    def test_query_is_url_encoded(self, monkeypatch):
        captured = _capture_request(monkeypatch)
        ArxivAdapter().fetch({"query": 'all:"embodied ai"'})
        # %22 引号整体编码,空格转 %20:交给 API 的是合法单参数
        assert "%22" in captured[0]
        assert ' ' not in captured[0].split("search_query=")[1].split("&")[0]

    def test_parses_atom_entries(self, monkeypatch):
        _capture_request(monkeypatch)
        items = ArxivAdapter().fetch({"query": 'all:"x"'})
        assert len(items) == 1
        item = items[0]
        assert item.title == "vision-language-action models survey"
        assert item.url == "http://arxiv.org/abs/2606.02578v1"
        assert item.content_text == "本篇综述讨论 VLA。第 1 行\n第 2 行"
        assert item.meta["categories"] == ["cs.RO", "cs.AI"]
        assert item.meta["all_authors"] == ["张三", "Li Si"]
        assert item.meta["pdf_url"] == "http://arxiv.org/pdf/2606.02578v1"
        assert item.published_at is not None


class TestCategoryMode:
    def test_backward_compatible_shape(self, monkeypatch):
        captured = _capture_request(monkeypatch)
        ArxivAdapter().fetch({"category": "cs.RO", "max_results": 5})
        params = _params(captured[0])
        assert params["search_query"] == ["cat:cs.RO"]
        assert params["max_results"] == ["5"]

    def test_default_category_is_cs_ai(self, monkeypatch):
        captured = _capture_request(monkeypatch)
        ArxivAdapter().fetch({})
        assert _params(captured[0])["search_query"] == ["cat:cs.AI"]


class TestBuildKeywordQuery:
    def test_embedded_quotes_are_stripped(self):
        assert build_keyword_query(['bad"quote"']) == 'all:"badquote"'

    def test_mixed_scripts_drop_non_ascii_words(self):
        q = build_keyword_query(["具身智能", "embodied ai", "机器人 VLA"])
        assert q == 'all:"embodied ai"'

    def test_all_non_ascii_returns_empty(self):
        assert build_keyword_query(["具身智能", "机器人"]) == ""
