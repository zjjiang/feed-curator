import re
import time
from typing import Any
import feedparser

from app.adapters.base import SourceAdapter, FetchedItem
from app.utils import outbound
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


def _load_feed(config: dict[str, Any]) -> str:
    """取 feed 内容:URL 走出网收口(条件 GET,304 → 返回空串),
    非 http 字符串视为已就地的 feed 内容(测试离线投喂)。"""
    feed_url = config["feed_url"]
    if not feed_url.startswith(("http://", "https://")):
        return feed_url
    headers = {"User-Agent": "feed-curator/0.1 (+rss)"}
    if config.get("etag"):
        headers["If-None-Match"] = config["etag"]
    if config.get("modified"):
        headers["If-Modified-Since"] = config["modified"]
    resp = outbound.request(feed_url, headers=headers, follow_redirects=True)
    if resp.status_code == 304:
        return ""
    return resp.text


class RSSAdapter(SourceAdapter):
    type = "rss"

    def fetch(self, config: dict[str, Any]) -> list[FetchedItem]:
        content = _load_feed(config)
        if not content:
            return []
        parsed = feedparser.parse(content)

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
