"""出网收口 outbound:代理探测优先级、境内外尝试顺序、连接错误回退语义。"""

import httpx
import pytest

from app.utils import outbound

_PROXY_ENV_VARS = (
    "OUTBOUND_PROXY", "CLASH_PROXY_PORT",
    "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
    "ALL_PROXY", "all_proxy",
)


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch):
    for var in _PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class Recorder:
    """按脚本逐个回放结果:Exception 抛出,否则视为 (status, content)。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        status, content = outcome
        return httpx.Response(status, content=content)


def _spy_clients(monkeypatch):
    """记录每个 Client 创建时的 proxy 参数后剥离再委托。

    httpx 会把 proxy 包在自定义 transport 外层,带着 proxy 参数委托会让
    MockTransport 被绕过真连代理主机;剥离后 IO 全由注入的 transport 接管。
    """
    created = []
    real_client = outbound.httpx.Client

    def spy(*args, **kwargs):
        created.append(kwargs.get("proxy"))
        kwargs.pop("proxy", None)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(outbound.httpx, "Client", spy)
    return created


class TestResolveProxy:
    def test_dedicated_var_wins(self, monkeypatch):
        monkeypatch.setenv("OUTBOUND_PROXY", "http://10.0.0.1:7890")
        monkeypatch.setenv("CLASH_PROXY_PORT", "7897")
        monkeypatch.setenv("HTTPS_PROXY", "http://ambient:1")
        assert outbound.resolve_proxy() == "http://10.0.0.1:7890"

    def test_clash_port_becomes_local_url(self, monkeypatch):
        monkeypatch.setenv("CLASH_PROXY_PORT", "7897")
        monkeypatch.setenv("HTTPS_PROXY", "http://ambient:1")
        assert outbound.resolve_proxy() == "http://127.0.0.1:7897"

    def test_standard_vars_as_fallback(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://sec:1")
        assert outbound.resolve_proxy() == "http://sec:1"
        monkeypatch.delenv("HTTPS_PROXY")
        monkeypatch.setenv("HTTP_PROXY", "http://plain:2")
        assert outbound.resolve_proxy() == "http://plain:2"
        monkeypatch.delenv("HTTP_PROXY")
        monkeypatch.setenv("ALL_PROXY", "http://all:3")
        assert outbound.resolve_proxy() == "http://all:3"

    def test_unset_returns_none(self):
        assert outbound.resolve_proxy() is None


class TestAttemptOrder:
    PROXY = "http://proxy:7897"

    def test_foreign_is_proxy_then_direct(self, monkeypatch):
        monkeypatch.setenv("OUTBOUND_PROXY", self.PROXY)
        created = _spy_clients(monkeypatch)
        rec = Recorder([httpx.ConnectError("代理已退出"), (200, b"ok")])

        resp = outbound.request("https://arxiv.org/api/query",
                                transport=httpx.MockTransport(rec))

        assert resp.status_code == 200
        assert created == [self.PROXY, None]
        assert rec.calls == 2

    def test_domestic_inferred_by_host_is_direct_then_proxy(self, monkeypatch):
        monkeypatch.setenv("OUTBOUND_PROXY", self.PROXY)
        created = _spy_clients(monkeypatch)
        rec = Recorder([httpx.ConnectError("直连被墙"), (200, b"ok")])

        resp = outbound.request("https://hf-mirror.com/api/daily_papers",
                                transport=httpx.MockTransport(rec))

        assert resp.status_code == 200
        assert created == [None, self.PROXY]

    def test_domestic_flag_overrides_host_inference(self, monkeypatch):
        monkeypatch.setenv("OUTBOUND_PROXY", self.PROXY)
        created = _spy_clients(monkeypatch)
        rec = Recorder([httpx.ConnectError("直连失败"), (200, b"ok")])

        outbound.request("https://example.com/api", domestic=True,
                         transport=httpx.MockTransport(rec))

        assert created == [None, self.PROXY]

    def test_no_proxy_configured_single_direct_attempt(self, monkeypatch):
        created = _spy_clients(monkeypatch)
        rec = Recorder([httpx.ConnectError("炸了"), (200, b"ok")])

        with pytest.raises(httpx.ConnectError):
            outbound.request("https://arxiv.org/api", transport=httpx.MockTransport(rec))

        assert created == [None]
        assert rec.calls == 1


class TestFailureSemantics:
    def test_all_attempts_fail_raises_last_transport_error(self, monkeypatch):
        monkeypatch.setenv("OUTBOUND_PROXY", "http://proxy:7897")
        _spy_clients(monkeypatch)
        rec = Recorder([httpx.ConnectTimeout("代理超时"), httpx.ConnectError("直连拒绝")])

        with pytest.raises(httpx.TransportError):
            outbound.request("https://arxiv.org/api", transport=httpx.MockTransport(rec))
        assert rec.calls == 2

    def test_http_status_error_does_not_retry(self, monkeypatch):
        monkeypatch.setenv("OUTBOUND_PROXY", "http://proxy:7897")
        _spy_clients(monkeypatch)
        rec = Recorder([(500, b"boom")])

        with pytest.raises(httpx.HTTPStatusError):
            outbound.request("https://arxiv.org/api", transport=httpx.MockTransport(rec))
        assert rec.calls == 1


class TestRedirects:
    def test_304_returned_without_raise(self):
        rec = Recorder([(304, b"")])
        resp = outbound.request("https://example.com/feed", transport=httpx.MockTransport(rec))
        assert resp.status_code == 304

    def test_default_does_not_follow(self):
        def handler(request):
            return httpx.Response(302, headers={"location": "https://example.com/final"})

        resp = outbound.request("https://example.com/go", transport=httpx.MockTransport(handler))
        assert resp.status_code == 302

    def test_follow_redirects_opt_in(self):
        def handler(request):
            if request.url.path == "/go":
                return httpx.Response(302, headers={"location": "https://example.com/final"})
            return httpx.Response(200, content=b"final")

        resp = outbound.request("https://example.com/go", follow_redirects=True,
                                transport=httpx.MockTransport(handler))
        assert resp.status_code == 200
