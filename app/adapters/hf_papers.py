"""Hugging Face Daily Papers 适配器(论文策展层)。

通过 hf-mirror.com 镜像调用 daily_papers JSON API(国内直连可达);
镜像网络失败或响应结构异常时,按回退链经代理直连 huggingface.co 官方域。
抓取窗口 = UTC 今天/昨天/前天三天:HF 的"日"按太平洋时间换日,三日窗口
数学上全覆盖(design 决策 6),跨日重复由文档身份层去重吸收。
出口地址可经管道 config.base_url 覆盖(spec 硬要求)。
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.adapters.base import FetchedItem, SourceAdapter
from app.utils import outbound
from app.utils.paper_identity import canonical_url_from_id

DEFAULT_BASE_URL = "https://hf-mirror.com"
OFFICIAL_BASE_URL = "https://huggingface.co"
WINDOW_DAYS = 3


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _window_dates(now: datetime) -> list[str]:
    """UTC 今天/昨天/前天,ISO 日期串。"""
    return [(now - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(WINDOW_DAYS)]


def _parse_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(
            value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


class HFPapersAdapter(SourceAdapter):
    type = "hf_papers"

    def fetch(self, config: dict[str, Any]) -> list[FetchedItem]:
        config = config or {}
        base_url = (config.get("base_url") or "").strip().rstrip("/")
        base_url = base_url or DEFAULT_BASE_URL
        items: list[FetchedItem] = []
        for date in _window_dates(_now_utc()):
            payload = self._load(base_url, date)
            if payload is None:
                # 镜像失败(网络/结构)→ 代理直连官方域
                payload = self._load(OFFICIAL_BASE_URL, date)
            if payload is None:
                raise RuntimeError(
                    f"HF Daily Papers 全部出口失败(镜像与官方域均不可达): {date}")
            items.extend(self._to_items(payload))
        return items

    def _load(self, base_url: str, date: str) -> list | None:
        """单日期榜单;返回 None 表示该出口失败(网络或结构)。"""
        url = f"{base_url}/api/daily_papers?date={date}"
        try:
            resp = outbound.request(url, timeout=30.0)
            payload = json.loads(resp.text)
        except (httpx.HTTPError, ValueError):
            return None
        return payload if isinstance(payload, list) else None

    def _to_items(self, entries: list) -> list[FetchedItem]:
        items: list[FetchedItem] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            paper = entry.get("paper") or {}
            items.append(self._to_item(paper, entry))
        return items

    def _to_item(self, paper: dict, entry: dict) -> FetchedItem:
        paper_id = str(paper.get("id") or "")
        url = canonical_url_from_id(paper_id) or ""
        if not url:
            # 条目级错误:不产生可用文档 URL,由采集层的条目隔离计为写入失败
            return FetchedItem(external_id=paper_id or "(无 id)",
                               title=str(paper.get("title") or "(无标题)"),
                               url="")
        authors = [a.get("name") for a in paper.get("authors") or []
                   if a.get("name")]
        summary = paper.get("summary") or ""
        extra: dict[str, Any] = {}
        for key in ("upvotes", "githubRepo", "githubStars"):
            if paper.get(key) is not None:
                extra[key] = paper[key]
        meta: dict[str, Any] = {
            "all_authors": authors,
            "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
        }
        if extra:
            meta["extra"] = extra
        return FetchedItem(
            external_id=paper_id,
            title=str(paper.get("title") or "(无标题)"),
            url=url,
            author=", ".join(authors[:3]) + ("..." if len(authors) > 3 else ""),
            description=summary[:500],
            content_text=summary,
            content_html=None,
            cover_image_url=None,
            published_at=_parse_epoch(paper.get("publishedAt")
                                      or entry.get("publishedAt")),
            meta=meta,
        )
