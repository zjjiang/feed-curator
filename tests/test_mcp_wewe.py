"""mcp_server 工具与 wewe_client 的测试(全部打桩,不触网不碰真库)。"""

import time

import httpx
import pytest

import app.mcp_server as mcp_mod
from app.models import Analysis, Doc, Domain, Pipe, RunLog

NOW = int(time.time())


@pytest.fixture()
def mcp_db(db_session, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=db_session.get_bind(),
                           autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(mcp_mod, "SessionLocal", factory)
    return db_session


@pytest.fixture()
def no_fetch(monkeypatch):
    monkeypatch.setattr(mcp_mod, "fetch_source", lambda db, pipe, trigger="auto": (3, None))


class TestPipeTools:
    def test_add_rss_validates_feed(self, mcp_db, monkeypatch):
        class FakeParsed:
            bozo = True
            entries = []

        monkeypatch.setattr(mcp_mod.feedparser, "parse", lambda *a, **kw: FakeParsed())
        r = mcp_mod.add_rss("测试", "https://x/feed")
        assert r["ok"] is False and "校验失败" in r["error"]
        assert mcp_db.query(Pipe).count() == 0

    def test_add_rss_success(self, mcp_db, no_fetch, monkeypatch):
        class FakeParsed:
            bozo = False
            entries = [object()]

        monkeypatch.setattr(mcp_mod.feedparser, "parse", lambda *a, **kw: FakeParsed())
        r = mcp_mod.add_rss("测试源", "https://x/feed")
        assert r["ok"] is True and r["inserted"] == 3
        pipe = mcp_db.get(Pipe, r["pipe_id"])
        assert pipe.name == "测试源"

    def test_add_rss_rejects_blank(self, mcp_db):
        assert mcp_mod.add_rss("", "https://x")["ok"] is False
        assert mcp_mod.add_rss("n", " ")["ok"] is False

    def test_list_pipes(self, mcp_db):
        mcp_db.add(Pipe(type="rss", name="R", config="{}", enabled=1,
                        created_at=NOW, updated_at=NOW))
        mcp_db.commit()
        r = mcp_mod.list_pipes()
        assert r["ok"] and r["count"] == 1 and r["pipes"][0]["name"] == "R"


class TestDomainTools:
    def test_create_and_update(self, mcp_db):
        r = mcp_mod.create_domain("具身智能", description="d", keywords="a, b，c")
        assert r["ok"] and r["keywords"] == ["a", "b", "c"]
        assert mcp_mod.create_domain("具身智能")["ok"] is False
        r = mcp_mod.update_domain_keywords("具身智能", "x, y")
        assert r["ok"] and r["keywords"] == ["x", "y"]
        d = mcp_db.query(Domain).filter_by(name="具身智能").one()
        import json
        assert json.loads(d.keywords) == ["x", "y"]

    def test_list_domains(self, mcp_db):
        mcp_mod.create_domain("D1")
        r = mcp_mod.list_domains()
        assert r["count"] == 1 and r["domains"][0]["name"] == "D1"


class TestRecommendAndStatus:
    def _seed(self, db):
        d = Doc(kind="article", url_key="https://e.com/a", url="https://e.com/a",
                title="好文", sort_time=NOW - 100, first_seen_at=NOW,
                last_modified_at=NOW)
        db.add(d)
        db.flush()
        db.add(Analysis(doc_id=d.id, status="ok", stars=5, summary="s",
                        keypoints='["k"]', domains='["具身智能"]', model="m",
                        prompt_version="v1", created_at=NOW))
        db.commit()
        return d.id

    def test_recommend(self, mcp_db):
        self._seed(mcp_db)
        r = mcp_mod.recommend_articles(days=7, min_stars=4)
        assert r["ok"] and r["count"] == 1
        assert r["articles"][0]["stars"] == 5
        r = mcp_mod.recommend_articles(days=7, min_stars=5, domain="不存在")
        assert r["count"] == 0
        assert mcp_mod.recommend_articles(days=0)["ok"] is False

    def test_job_status(self, mcp_db):
        self._seed(mcp_db)
        mcp_db.add(RunLog(kind="analyze", status="done", total=2, processed=2,
                          succeeded=1, failed=1, created_at=NOW,
                          finished_at=NOW))
        mcp_db.commit()
        r = mcp_mod.job_status()
        assert r["ok"] and r["pending"] == 0
        assert r["run"]["percent"] == 100

    def test_save_url_tool(self, mcp_db):
        r = mcp_mod.save_url("https://github.com/o/p")
        assert r["kind"] == "repo" and r["created"]   # manual 管道懒创建


# ============ wewe_client ============

from app.services.wewe_client import WeweClient, WeweError, _extract_list


def _client_with(monkeypatch, handler) -> tuple[WeweClient, list]:
    calls = []
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def tracking_client(**kw):
        return real_client(transport=transport, timeout=5)

    monkeypatch.setattr("app.services.wewe_client.httpx.Client", tracking_client)
    return WeweClient(base_url="http://wewe.test"), calls


class TestWeweClient:
    def test_login_and_token_reuse(self, monkeypatch):
        state = {"logins": 0}

        def handler(request):
            if request.url.path.endswith("/auth/token"):
                state["logins"] += 1
                return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
            return httpx.Response(200, json={"data": {"list": []}})

        client, _ = _client_with(monkeypatch, handler)
        client.search("机器人")
        client.search("AI")
        assert state["logins"] == 1   # 第二次复用 token

    def test_search_parses_candidates(self, monkeypatch):
        def handler(request):
            if request.url.path.endswith("/auth/token"):
                return httpx.Response(200, json={"access_token": "T"})
            return httpx.Response(200, json={"data": {"list": [
                {"fakeid": "f1", "nickname": "号一", "alias": "a", "signature": "s"},
                {"nickname": "无id被跳过"},
            ]}})

        client, _ = _client_with(monkeypatch, handler)
        candidates = client.search("机器人")
        assert len(candidates) == 1
        assert candidates[0].to_dict()["fakeid"] == "f1"

    def test_search_empty_keyword(self, monkeypatch):
        client, _ = _client_with(
            monkeypatch, lambda r: httpx.Response(200, json={"access_token": "T"}))
        with pytest.raises(WeweError):
            client.search("  ")

    def test_login_failure_wrapped(self, monkeypatch):
        def handler(request):
            raise httpx.ConnectError("down")

        client, _ = _client_with(monkeypatch, handler)
        with pytest.raises(WeweError, match="无法连接"):
            client.search("x")

    def test_subscribe(self, monkeypatch):
        def handler(request):
            if request.url.path.endswith("/auth/token"):
                return httpx.Response(200, json={"access_token": "T"})
            if request.url.path == "/api/v1/wx/mps" and request.method == "POST":
                return httpx.Response(200, json={"code": 0, "data": {"id": "MP_WXS_1"}})
            return httpx.Response(200, json={})

        client, _ = _client_with(monkeypatch, handler)
        assert client.subscribe("名称", "fake") == "MP_WXS_1"
        with pytest.raises(WeweError):
            client.subscribe("", "fake")

    def test_trigger_update_swallows_errors(self, monkeypatch):
        def handler(request):
            if request.url.path.endswith("/auth/token"):
                return httpx.Response(200, json={"access_token": "T"})
            raise httpx.ReadTimeout("t")

        client, _ = _client_with(monkeypatch, handler)
        client.trigger_update("MP_1")   # 不应抛


class TestExtractList:
    def test_variants(self):
        assert _extract_list([1, 2]) == [1, 2]
        assert _extract_list({"data": [1]}) == [1]
        assert _extract_list({"data": {"list": [1]}}) == [1]
        assert _extract_list({"data": {"items": [1]}}) == [1]
        assert _extract_list({"data": {"rows": []}}) == []
        assert _extract_list("junk") == []
        assert _extract_list({}) == []
