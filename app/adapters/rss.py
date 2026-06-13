import time
from typing import Any
import feedparser

from app.adapters.base import SourceAdapter, FetchedItem
from app.utils.html_clean import html_to_text


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
                meta={
                    "tags": [t.term for t in entry.get("tags", []) if t.get("term")],
                },
            )
            items.append(item)

        return items
