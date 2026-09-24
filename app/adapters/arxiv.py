import time
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

from app.adapters.base import SourceAdapter, FetchedItem
from app.utils import outbound
from app.utils.html_clean import html_to_text

ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

DEFAULT_MAX_RESULTS = 30


def build_keyword_query(keywords: list[str]) -> str:
    """ASCII 关键词 → `all:"短语" OR ...` 检索串;全非 ASCII 返回空串。

    逐词剥内嵌引号防语法注入;OR 连接,不依赖括号分组(design 实测分组行为存疑)。
    """
    terms = []
    for kw in keywords:
        kw = kw.strip().replace('"', "")
        if kw and kw.isascii():
            terms.append(f'all:"{kw}"')
    return " OR ".join(terms)


def _api_url(search_query: str, max_results: int) -> str:
    return ("https://export.arxiv.org/api/query?"
            + f"search_query={quote(search_query, safe='')}"
            + f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}")


class ArxivAdapter(SourceAdapter):
    type = "arxiv"

    def fetch(self, config: dict[str, Any]) -> list[FetchedItem]:
        config = config or {}
        max_results = int(config.get("max_results", DEFAULT_MAX_RESULTS))
        query = config.get("query")
        if query is not None:
            # 派生管道(或显式配置 query 的共享管道):关键词模式
            query = query.strip()
            if not query:
                raise ValueError("arxiv 派生管道无可用的 ASCII 关键词,本周期空转"
                                 "(在领域关键词中补充英文关键词后恢复)")
            return self._fetch(_api_url(query, max_results))
        category = config.get("category", "cs.AI")
        return self._fetch(_api_url(f"cat:{category}", max_results))

    def _fetch(self, url: str) -> list[FetchedItem]:
        resp = outbound.request(url, timeout=30.0)
        return self._parse(resp.text)

    def _parse(self, xml_text: str) -> list[FetchedItem]:
        root = ET.fromstring(xml_text)
        items: list[FetchedItem] = []

        for entry in root.findall("atom:entry", ARXIV_NS):
            arxiv_id = entry.findtext("atom:id", "", ARXIV_NS).strip()
            title = entry.findtext("atom:title", "", ARXIV_NS).strip().replace("\n", " ")
            summary = entry.findtext("atom:summary", "", ARXIV_NS).strip()
            published = entry.findtext("atom:published", "", ARXIV_NS).strip()

            authors = [
                a.findtext("atom:name", "", ARXIV_NS)
                for a in entry.findall("atom:author", ARXIV_NS)
            ]

            links = entry.findall("atom:link", ARXIV_NS)
            pdf_url = ""
            abs_url = arxiv_id
            for link in links:
                if link.get("title") == "pdf":
                    pdf_url = link.get("href", "")
                elif link.get("rel") == "alternate":
                    abs_url = link.get("href", arxiv_id)

            categories = [
                c.get("term", "")
                for c in entry.findall("atom:category", ARXIV_NS)
                if c.get("term")
            ]

            pub_ts = _parse_arxiv_time(published)

            item = FetchedItem(
                external_id=arxiv_id,
                title=title,
                url=abs_url,
                author=", ".join(authors[:3]) + ("..." if len(authors) > 3 else ""),
                description=summary[:500],
                content_text=summary,
                content_html=None,
                cover_image_url=None,
                published_at=pub_ts,
                meta={
                    "categories": categories,
                    "pdf_url": pdf_url,
                    "all_authors": authors,
                },
            )
            items.append(item)

        return items


def _parse_arxiv_time(s: str) -> int | None:
    if not s:
        return None
    try:
        from datetime import datetime
        normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
        dt = datetime.fromisoformat(normalized)
        return int(dt.timestamp())
    except Exception:
        return None
