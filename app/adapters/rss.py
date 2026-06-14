import re
import time
from typing import Any
import feedparser

from app.adapters.base import SourceAdapter, FetchedItem
from app.utils.html_clean import html_to_text


# RSSHub 的 GitHub Trending 等源会把仓库元信息以 "Stars: 123" 形式塞进正文。
# 这里做宽容提取：命中才写入 meta，对不含这些字段的普通 RSS 完全无副作用。
_RE_STARS = re.compile(r"Stars?\s*[:：]\s*([\d,]+)", re.IGNORECASE)
_RE_FORKS = re.compile(r"Forks?\s*[:：]\s*([\d,]+)", re.IGNORECASE)
_RE_LANG = re.compile(r"Language\s*[:：]\s*([^\n\r]+)", re.IGNORECASE)


def _extract_repo_meta(text: str) -> dict[str, Any]:
    """从正文中提取 GitHub 仓库元信息（stars/forks/language）。无则返回空字典。"""
    if not text:
        return {}
    meta: dict[str, Any] = {}
    m = _RE_STARS.search(text)
    if m:
        try:
            meta["stars"] = int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = _RE_FORKS.search(text)
    if m:
        try:
            meta["forks"] = int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = _RE_LANG.search(text)
    if m:
        lang = m.group(1).strip()
        if lang:
            meta["language"] = lang
    return meta


def _parse_time(struct_time) -> int | None:
    if not struct_time:
        return None
    try:
        return int(time.mktime(struct_time))
    except Exception:
        return None


def _pick_content_html(entry) -> str:
    if "content" in entry and entry.content:
        for block in entry.content:
            value = block.get("value")
            if value:
                return value
    summary = entry.get("summary_detail")
    if summary and summary.get("value"):
        return summary["value"]
    return entry.get("summary") or ""


def _pick_cover_image(entry, content_html: str) -> str | None:
    if "media_thumbnail" in entry:
        thumb = entry.media_thumbnail
        if thumb and thumb[0].get("url"):
            return thumb[0]["url"]
    enclosures = entry.get("enclosures") or []
    for enc in enclosures:
        href = enc.get("href")
        type_ = enc.get("type") or ""
        if href and type_.startswith("image/"):
            return href
    return None


class RSSAdapter(SourceAdapter):
    type = "rss"

    def fetch(self, config: dict[str, Any]) -> list[FetchedItem]:
        feed_url = config["feed_url"]
        etag = config.get("etag")
        modified = config.get("modified")

        parsed = feedparser.parse(
            feed_url,
            etag=etag,
            modified=modified,
            request_headers={"User-Agent": "feed-curator/0.1 (+rss)"},
        )

        items: list[FetchedItem] = []
        for entry in parsed.entries:
            external_id = (
                entry.get("id")
                or entry.get("guid")
                or entry.get("link")
                or entry.get("title")
            )
            if not external_id:
                continue

            content_html = _pick_content_html(entry)
            content_text = html_to_text(content_html)

            published = _parse_time(entry.get("published_parsed")) or _parse_time(
                entry.get("updated_parsed")
            )

            meta: dict[str, Any] = {
                "tags": [t.term for t in entry.get("tags", []) if t.get("term")],
            }
            # 宽容提取 GitHub 仓库元信息（stars/forks/language），命中才写入
            repo_meta = _extract_repo_meta(content_text)
            if repo_meta:
                meta.update(repo_meta)

            item = FetchedItem(
                external_id=str(external_id),
                title=entry.get("title", "(无标题)").strip(),
                url=entry.get("link") or "",
                author=entry.get("author"),
                description=html_to_text(entry.get("summary"))[:500] or None,
                content_text=content_text,
                content_html=content_html,
                cover_image_url=_pick_cover_image(entry, content_html),
                published_at=published,
                meta=meta,
            )
            items.append(item)

        return items
