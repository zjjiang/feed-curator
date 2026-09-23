from urllib.parse import urlsplit

from typing import Literal

Kind = Literal["paper", "repo", "article"]

# abs/pdf/html 均指向同一篇论文，判成 article 会导致同论文两个 doc
_ARXIV_PAPER_SECTIONS = frozenset({"abs", "pdf", "html"})


def detect_kind(url: str) -> Kind:
    """由 URL 判定实体类型：arXiv 论文页 → paper、仓库根路径 → repo、其余 → article。

    实体类型由内容（URL）判定，与采集管道类型无关（design.md 决策 9）。
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    segments = [s for s in parts.path.split("/") if s]

    if host == "arxiv.org":
        if segments and segments[0] in _ARXIV_PAPER_SECTIONS:
            return "paper"
        return "article"

    if host == "github.com":
        # 恰好 owner/repo 两段才是仓库根路径；issues/blob/tree/gist 等都是普通页面
        if len(segments) == 2:
            return "repo"
        return "article"

    return "article"
