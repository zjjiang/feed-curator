"""GitHub 仓库检索适配器。

检索串组装(design 决策七):仅 ASCII 关键词参与(中文在 GitHub 检索
无收益);含空格词条加引号做短语匹配,词条间 OR 连接;叠加热度窗口
(created:>=)与最低星标(stars:>=),随采集时刻滚动;总长截到 GitHub
256 上限内,不产生残缺引号或悬空 OR。适配器只产出 FetchedItem,不碰 DB。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.adapters.base import FetchedItem, SourceAdapter
from app.services.github_client import GitHubClient

MAX_QUERY_LEN = 256
# GitHub 限制单条检索最多 5 个 AND/OR/NOT 算子 → 最多 6 个词条(实测 7 词即 422)
MAX_TERMS = 6
DEFAULT_WINDOW_DAYS = 90
DEFAULT_MIN_STARS = 30
DEFAULT_PER_PAGE = 30


def ascii_keywords(keywords: list[str] | None) -> list[str]:
    """仅保留全 ASCII 的词条;中英混合词条整体丢弃。"""
    if not keywords:
        return []
    return [k.strip() for k in keywords if k.strip() and k.isascii()]


def _quote_term(term: str) -> str:
    return f'"{term}"' if " " in term else term


def _truncate_expr(expr: str, budget: int) -> str:
    """截到 budget 内:不留下残缺引号,也不留悬空的 OR。"""
    if len(expr) <= budget:
        return expr
    cut = expr[:budget]
    if cut.count('"') % 2 == 1:
        cut = cut[:cut.rfind('"')]
    cut = cut.rstrip()
    if cut.endswith("OR"):
        cut = cut[:-2].rstrip()
    return cut


def build_query(keywords: list[str] | None, *, window_days: int,
                min_stars: int, now: datetime | None = None) -> str | None:
    """领域关键词 + 热度限定符 → 最终检索串。无可用的 ASCII 关键词时为 None。

    关键词按重要性排序,超出 MAX_TERMS 的尾部词条被舍弃。
    """
    terms = [_quote_term(k) for k in ascii_keywords(keywords)][:MAX_TERMS]
    if not terms:
        return None
    now = now or datetime.now(UTC)
    since = (now - timedelta(days=window_days)).strftime("%Y-%m-%d")
    qualifiers = f"created:>={since} stars:>={min_stars}"
    expr = _truncate_expr(" OR ".join(terms), MAX_QUERY_LEN - len(qualifiers) - 1)
    expr = expr.rstrip()
    if not expr:
        return None
    return f"{expr} {qualifiers}"


def _parse_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


class GitHubAdapter(SourceAdapter):
    type = "github"

    def fetch(self, config: dict[str, Any]) -> list[FetchedItem]:
        config = config or {}
        query = self._query_from_config(config)
        if not query:
            raise ValueError("github 管道缺少可用检索条件"
                             "(派生领域无 ASCII 关键词,或共享管道未配置 query)")
        per_page = int(config.get("per_page", DEFAULT_PER_PAGE))
        min_stars = config.get("min_stars")
        with GitHubClient() as client:
            rows = client.search_repositories(query, per_page=per_page)
        items = []
        for row in rows:
            if min_stars is not None and \
                    (row.get("stargazers_count") or 0) < min_stars:
                continue
            items.append(self._to_item(row))
        return items

    @staticmethod
    def _query_from_config(config: dict) -> str | None:
        keywords = config.get("keywords")
        if keywords:
            return build_query(
                keywords,
                window_days=int(config.get("window_days", DEFAULT_WINDOW_DAYS)),
                min_stars=int(config.get("min_stars", DEFAULT_MIN_STARS)),
            )
        return (config.get("query") or "").strip() or None

    @staticmethod
    def _to_item(row: dict) -> FetchedItem:
        license_info = row.get("license") or {}
        spdx = license_info.get("spdx_id")
        return FetchedItem(
            external_id=row["full_name"],
            title=row.get("full_name") or "",
            url=row.get("html_url") or "",
            description=row.get("description"),
            published_at=_parse_epoch(row.get("pushed_at")),
            meta={
                "stars": row.get("stargazers_count"),
                "forks": row.get("forks_count"),
                "open_issues": row.get("open_issues_count"),
                "language": row.get("language"),
                "topics": row.get("topics") or [],
                "license": spdx if spdx and spdx != "NOASSERTION" else None,
            },
        )
