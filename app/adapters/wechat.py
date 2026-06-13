import time
from typing import Any
import httpx

from app.adapters.base import SourceAdapter, FetchedItem
from app.utils.html_clean import html_to_text


class WechatAdapter(SourceAdapter):
    type = "wechat"

    def fetch(self, config: dict[str, Any]) -> list[FetchedItem]:
        mp_id = config["mp_id"]
        base_url = config.get("wewe_base_url", "http://localhost:9001").rstrip("/")
        url = f"{base_url}/feed/{mp_id}.json"

        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()

        items_raw = data.get("items") or []
        items: list[FetchedItem] = []
        for entry in items_raw:
            external_id = entry.get("id") or entry.get("link")
            if not external_id:
                continue

            content_html = entry.get("content") or ""
            content_text = html_to_text(content_html) if content_html else ""

            published_str = entry.get("updated") or entry.get("date_published")
            published = _parse_iso8601(published_str)

            channel_name = entry.get("channel_name")
            feed_info = entry.get("feed") or {}

            item = FetchedItem(
                external_id=str(external_id),
                title=(entry.get("title") or "(无标题)").strip(),
                url=entry.get("link") or "",
                author=channel_name or feed_info.get("name"),
                description=(entry.get("description") or "")[:500] or None,
                content_text=content_text,
                content_html=content_html,
                cover_image_url=feed_info.get("cover"),
                published_at=published,
                meta={
                    "mp_id": mp_id,
                    "channel_name": channel_name,
                    "feed_intro": feed_info.get("intro"),
                },
            )
            items.append(item)
        return items


def _parse_iso8601(s: str | None) -> int | None:
    if not s:
        return None
    try:
        from datetime import datetime
        normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
        dt = datetime.fromisoformat(normalized)
        return int(dt.timestamp())
    except Exception:
        return None
