import time
from typing import Any
import httpx
from xml.etree import ElementTree as ET

from app.adapters.base import SourceAdapter, FetchedItem
from app.utils.html_clean import html_to_text

ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


class ArxivAdapter(SourceAdapter):
    type = "arxiv"

    def fetch(self, config: dict[str, Any]) -> list[FetchedItem]:
        category = config.get("category", "cs.AI")
        max_results = config.get("max_results", 30)
        url = f"https://export.arxiv.org/api/query?search_query=cat:{category}&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"

        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url)
            resp.raise_for_status()

        root = ET.fromstring(resp.text)
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
