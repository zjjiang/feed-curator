"""论文身份规范化:同一 arXiv 论文的多形态 URL 归并为规范身份。

HF 给 id、RSS 甩 pdf 链接、arXiv API 给带版本 abs、邮件/博客用 export/www
host——现有去重键是纯 URL 归一化,挡不住这些形态。入库前(arXiv 链接在
判型为 paper 后)统一为 https://arxiv.org/abs/{id},doc.url/url_key 由此
跨源天然去重;实体字段解析仍用原始 URL(保留 version 等信息)。
normalize_url 本身不动——存量 url_key 的稳定性优先(design 决策 1/2)。
"""

import re
from urllib.parse import urlsplit

_ARXIV_HOSTS = frozenset({"arxiv.org", "www.arxiv.org", "export.arxiv.org"})
_ARXIV_PAPER_SECTIONS = frozenset({"abs", "pdf", "html"})
_ARXIV_ID = re.compile(r"([0-9]{4}\.[0-9]{4,5})(?:v\d+)?")


def canonical_paper_url(url: str) -> str:
    """arXiv 论文链接 → 规范身份 URL(剥版本);其余 URL 一律原样返回。"""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if host not in _ARXIV_HOSTS or len(segments) < 2 \
            or segments[0] not in _ARXIV_PAPER_SECTIONS:
        return url
    m = _ARXIV_ID.fullmatch(segments[1])
    if m is None:
        return url
    return f"https://arxiv.org/abs/{m.group(1)}"
