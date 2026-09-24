"""GitHub REST API 客户端:搜索仓库 / 仓库详情 / README。

纯 HTTP,不碰 DB。token 从 GITHUB_TOKEN 环境变量读取(可选,缺失时以
匿名配额运行)。触发限流(403/429 且配额耗尽)时抛 RateLimitError,
由调用方决定退避或停轮——客户端自身不做进程内重试,避免重试风暴。
"""

import os

import httpx

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
# README 落库上限:AI 判定只用 3000 字预览,超长 README 截断存储
MAX_README_CHARS = 200_000


class GitHubError(Exception):
    """GitHub 访问失败基类。"""


class RateLimitError(GitHubError):
    """配额耗尽。携带重置时间戳(GitHub 返回的 unix 秒)。"""

    def __init__(self, reset_at: int):
        self.reset_at = reset_at
        super().__init__(f"github 限流,约 {reset_at} 重置")


class GitHubClient:
    """单个客户端持有一条 httpx 连接池,支持 with 语法。"""

    def __init__(self, token: str | None = None, timeout: float = 15.0,
                 transport: httpx.BaseTransport | None = None):
        if token is None:
            token = (os.environ.get("GITHUB_TOKEN") or "").strip() or None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # follow_redirects:仓库改名后 API 返回 301 到新地址,必须跟随
        self._client = httpx.Client(base_url=API_BASE, headers=headers,
                                    timeout=timeout, transport=transport,
                                    follow_redirects=True)

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def search_repositories(self, query: str, *, per_page: int = 30) -> list[dict]:
        """按检索串搜索仓库,按星标降序,返回原始 items 列表(单页)。"""
        data = self._request_json("GET", "/search/repositories", params={
            "q": query, "sort": "stars", "order": "desc", "per_page": per_page,
        })
        return data.get("items", [])

    def get_repo(self, owner: str, name: str) -> dict:
        return self._request_json("GET", f"/repos/{owner}/{name}")

    def get_readme(self, owner: str, name: str) -> str | None:
        """直取 raw markdown;仓库无 README 返回 None。"""
        resp = self._client.get(
            f"/repos/{owner}/{name}/readme",
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if resp.status_code == 404:
            return None
        self._raise_for_rate_limit(resp)
        resp.raise_for_status()
        return resp.text[:MAX_README_CHARS]

    def _request_json(self, method: str, path: str, **kwargs) -> dict:
        resp = self._client.request(method, path, **kwargs)
        self._raise_for_rate_limit(resp)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _raise_for_rate_limit(resp: httpx.Response) -> None:
        if resp.status_code in (403, 429) and \
                resp.headers.get("x-ratelimit-remaining") == "0":
            reset = int(resp.headers.get("x-ratelimit-reset") or 0)
            raise RateLimitError(reset)
