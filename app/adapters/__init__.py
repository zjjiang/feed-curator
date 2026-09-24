from app.adapters.base import SourceAdapter, FetchedItem
from app.adapters.rss import RSSAdapter
from app.adapters.wechat import WechatAdapter
from app.adapters.arxiv import ArxivAdapter
from app.adapters.github import GitHubAdapter

ADAPTERS: dict[str, SourceAdapter] = {
    "rss": RSSAdapter(),
    "wechat": WechatAdapter(),
    "arxiv": ArxivAdapter(),
    "github": GitHubAdapter(),
}


def get_adapter(source_type: str) -> SourceAdapter:
    if source_type not in ADAPTERS:
        raise ValueError(f"未知的 source type: {source_type}")
    return ADAPTERS[source_type]


__all__ = ["ADAPTERS", "get_adapter", "SourceAdapter", "FetchedItem"]