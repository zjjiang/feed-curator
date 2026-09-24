"""GitHubClient 的 MockTransport 单测:请求构造、限流识别、raw README。"""

import httpx
import pytest

from app.services.github_client import (
    MAX_README_CHARS,
    GitHubClient,
    RateLimitError,
)

SEARCH_BODY = {
    "total_count": 1,
    "incomplete_results": False,
    "items": [{
        "id": 1,
        "full_name": "acme/vla-robot",
        "html_url": "https://github.com/acme/vla-robot",
        "description": "A VLA model",
        "stargazers_count": 1234,
        "forks_count": 56,
        "open_issues_count": 7,
        "language": "Python",
        "topics": ["vla", "robotics"],
        "license": {"spdx_id": "MIT"},
        "pushed_at": "2026-09-20T08:00:00Z",
        "created_at": "2026-09-18T08:00:00Z",
    }],
}


def _client(handler, token="tok"):
    return GitHubClient(token=token, transport=httpx.MockTransport(handler))


def test_search_sends_expected_params_and_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=SEARCH_BODY)

    with _client(handler, token="tok-123") as c:
        items = c.search_repositories("VLA stars:>50", per_page=30)

    assert seen["auth"] == "Bearer tok-123"
    assert "q=VLA" in seen["url"]
    assert "sort=stars" in seen["url"]
    assert "per_page=30" in seen["url"]
    assert items[0]["full_name"] == "acme/vla-robot"


def test_no_token_sends_no_auth_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") is None
        return httpx.Response(200, json=SEARCH_BODY)

    with _client(handler, token=None) as c:
        assert c.search_repositories("x") == SEARCH_BODY["items"]


def test_search_rate_limited_raises_rate_limit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={
            "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000000"})

    with _client(handler) as c:
        with pytest.raises(RateLimitError):
            c.search_repositories("x")


def test_search_429_counts_as_rate_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={
            "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"})

    with _client(handler) as c:
        with pytest.raises(RateLimitError):
            c.search_repositories("x")


def test_403_without_quota_is_plain_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    import httpx as _httpx

    with _client(handler) as c:
        with pytest.raises(_httpx.HTTPStatusError):
            c.search_repositories("x")


def test_get_repo_returns_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/vla-robot"
        return httpx.Response(200, json={"full_name": "acme/vla-robot",
                                         "stargazers_count": 2000})

    with _client(handler) as c:
        repo = c.get_repo("acme", "vla-robot")
    assert repo["stargazers_count"] == 2000


def test_get_readme_returns_raw_markdown():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/vla-robot/readme"
        assert request.headers["Accept"] == "application/vnd.github.raw+json"
        return httpx.Response(200, text="# VLA Robot\n\n安装说明")

    with _client(handler) as c:
        assert c.get_readme("acme", "vla-robot") == "# VLA Robot\n\n安装说明"


def test_get_readme_404_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with _client(handler) as c:
        assert c.get_readme("acme", "empty") is None


def test_get_readme_truncates_extremely_long_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * (MAX_README_CHARS + 5000))

    with _client(handler) as c:
        assert len(c.get_readme("acme", "huge")) == MAX_README_CHARS


def test_get_repo_rate_limited():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={
            "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"})

    with _client(handler) as c:
        with pytest.raises(RateLimitError):
            c.get_repo("acme", "vla-robot")


def test_get_repo_follows_rename_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/old/name":
            return httpx.Response(301, headers={
                "Location": "https://api.github.com/repositories/123"})
        assert request.url.path == "/repositories/123"
        return httpx.Response(200, json={"full_name": "new/name"})

    with _client(handler) as c:
        assert c.get_repo("old", "name")["full_name"] == "new/name"
