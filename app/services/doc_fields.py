"""FetchedItem → (kind, detail) 的字段分流。

实体类型由 URL 判定(doc_kind.detect_kind),字段按实体表结构分流;
meta 里已拆入一等列的 key 不再冗余进 source_meta。迁移脚本、采集层、
手工存入共用同一套规则(迁移脚本为一次性历史产物,自带同逻辑副本)。
"""

import re
from urllib.parse import urlsplit

from app.utils.doc_kind import detect_kind
from app.utils.json_str import json_dump

_RE_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5})(v\d+)?",
                       re.IGNORECASE)

# 已拆入实体一等列的 meta key
_META_LIFTED = frozenset({"all_authors", "categories", "pdf_url", "stars", "forks",
                          "open_issues", "language", "topics", "license"})


def detect_doc_kind(url: str) -> str:
    return detect_kind(url)


def parse_arxiv_id(url: str) -> tuple[str | None, str | None]:
    m = _RE_ARXIV.search(url or "")
    if not m:
        return None, None
    return m.group(1), m.group(2)


def parse_github_owner_name(url: str) -> tuple[str | None, str | None]:
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    if parts.hostname == "github.com" and len(segs) >= 2:
        return segs[0], segs[1]
    return None, None


def build_detail(
    kind: str,
    *,
    url: str,
    author: str | None = None,
    description: str | None = None,
    content_text: str | None = None,
    content_html: str | None = None,
    cover_image_url: str | None = None,
    word_count: int = 0,
    published_at: int | None = None,
    meta: dict | None = None,
) -> dict:
    """按实体类型把原始字段组装成实体表的 detail 字典。"""
    meta = meta or {}
    if kind == "paper":
        arxiv_id, version = parse_arxiv_id(url)
        return {
            "abstract": description,
            "content_text": content_text,
            "authors": json_dump(meta.get("all_authors") or []),
            "categories": json_dump(meta.get("categories") or []),
            "pdf_url": meta.get("pdf_url"),
            "arxiv_id": arxiv_id,
            "version": version,
            "submitted_at": published_at,
        }
    if kind == "repo":
        owner, name = parse_github_owner_name(url)
        return {
            "owner": owner,
            "name": name,
            "description": description,
            "readme_text": content_text,
            "stars": meta.get("stars"),
            "forks": meta.get("forks"),
            "open_issues": meta.get("open_issues"),
            "language": meta.get("language"),
            "topics": json_dump(meta["topics"]) if meta.get("topics") else None,
            "license": meta.get("license"),
            "pushed_at": published_at,
        }
    source_meta = {k: v for k, v in meta.items() if k not in _META_LIFTED}
    return {
        "author": author,
        "description": description,
        "content_text": content_text,
        "content_html": content_html,
        "cover_image_url": cover_image_url,
        "word_count": word_count,
        "published_at": published_at,
        "source_meta": json_dump(source_meta) if source_meta else None,
    }
