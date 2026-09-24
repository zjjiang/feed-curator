from app.adapters.base import SourceAdapter, FetchedItem
from app.adapters.rss import RSSAdapter
from app.adapters.wechat import WechatAdapter
from app.adapters.arxiv import ArxivAdapter
from app.adapters.github import GitHubAdapter
from app.adapters.hf_papers import HFPapersAdapter

ADAPTERS: dict[str, SourceAdapter] = {
    "rss": RSSAdapter(),
    "wechat": WechatAdapter(),
    "arxiv": ArxivAdapter(),
    "github": GitHubAdapter(),
    "hf_papers": HFPapersAdapter(),
}


def get_adapter(source_type: str) -> SourceAdapter:
    if source_type not in ADAPTERS:
        raise ValueError(f"未知的 source type: {source_type}")
    return ADAPTERS[source_type]


__all__ = ["ADAPTERS", "get_adapter", "SourceAdapter", "FetchedItem"]