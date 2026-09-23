import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_session
from app.models import Analysis, Article, Doc, Domain, Pipe, Reading, RunLog

NOW = int(time.time())
OLD = NOW - 10 * 86400


def _doc(db, *, kind="article", url="https://example.com/a", title="标题",
         word_count=1200, content="正文内容" * 100, kind_tag=None, stars=None):
    doc = Doc(kind=kind, url_key=url, url=url, title=title,
              sort_time=NOW - 3600, first_seen_at=OLD, last_modified_at=OLD)
    db.add(doc)
    db.flush()
    if kind == "article":
        db.add(Article(id=doc.id, content_text=content, word_count=word_count,
                       kind_tag=kind_tag))
    elif kind == "repo":
        from app.models import Repo
        db.add(Repo(id=doc.id, owner="o", name="n", readme_text=content))
    else:
        from app.models import Paper
        db.add(Paper(id=doc.id, abstract=content))
    if stars is not None:
        db.add(Analysis(doc_id=doc.id, status="ok", summary="摘要",
                        keypoints='["k1"]', domains='[]', stars=stars,
                        model="m", prompt_version="v1", created_at=NOW))
    db.commit()
    return doc


@pytest.fixture()
def client(db_session, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestPages:
    def test_index_lists_docs(self, client, db_session):
        _doc(db_session)
        html = client.get("/").text
        assert "标题" in html

    def test_index_filters_dismissed_and_favorites(self, client, db_session):
        d = _doc(db_session, url="https://example.com/fav")
        db_session.add(Reading(doc_id=d.id, is_favorite=1, updated_at=NOW))
        d2 = _doc(db_session, url="https://example.com/gone")
        db_session.add(Reading(doc_id=d2.id, is_dismissed=1, updated_at=NOW))
        db_session.commit()

        assert "fav" in client.get("/?favorites=1").text
        assert "gone" not in client.get("/").text

    def test_kind_tag_filter_excludes_untagged(self, client, db_session):
        _doc(db_session, kind_tag="tech", title="技术文",
             url="https://example.com/tech")
        _doc(db_session, kind_tag=None, title="无子类",
             url="https://example.com/none")
        html = client.get("/?kind_tag=tech").text
        assert "技术文" in html and "无子类" not in html

    def test_kind_tag_filter_only_for_articles(self, client, db_session):
        _doc(db_session)
        # 文章或全部类型:显示子类筛选
        assert "全部子类" in client.get("/").text
        assert "全部子类" in client.get("/?kind=article").text
        # 论文/项目没有子类概念:隐藏
        assert "全部子类" not in client.get("/?kind=paper").text
        assert "全部子类" not in client.get("/?kind=repo").text

    def test_doc_page(self, client, db_session):
        d = _doc(db_session, stars=4)
        html = client.get(f"/docs/{d.id}").text
        assert "标题" in html and "摘要" in html and "正文内容" in html

    def test_domains_page_and_create(self, client, db_session):
        assert client.get("/admin/domains").text
        client.post("/admin/domains/add", data={"name": "新领域",
                                          "keywords": "a, b，c"})
        d = db_session.query(Domain).filter_by(name="新领域").one()
        import json
        assert json.loads(d.keywords) == ["a", "b", "c"]
        # 重名被拒
        client.post("/admin/domains/add", data={"name": "新领域"})
        assert db_session.query(Domain).filter_by(name="新领域").count() == 1

    def test_pipes_page_manual_save_and_toggle(self, client, db_session,
                                               monkeypatch):
        db_session.add(Pipe(type="rss", name="R", config="{}",
                            created_at=NOW, updated_at=NOW))
        db_session.commit()

        assert client.get("/admin/pipes").text
        # 手工存入 github URL → repo(不触网)
        client.post("/admin/pipes/save-url", data={"url": "https://github.com/acme/repo-x"})
        from app.models import Repo
        assert db_session.query(Repo).count() == 1
        # 停用切换
        client.post("/admin/pipes/1/toggle")
        assert db_session.get(Pipe, 1).enabled == 0

    def test_ops_page(self, client, db_session):
        _doc(db_session)
        db_session.add(RunLog(kind="fetch", pipe_name="p", status="done",
                              inserted=3, duration_ms=120, created_at=NOW))
        db_session.commit()
        html = client.get("/admin").text
        assert "待判定" in html and "p" in html

    def test_legacy_admin_paths_redirect(self, client):
        for old, new in (("/ops", "/admin"), ("/pipes", "/admin/pipes"),
                         ("/domains", "/admin/domains")):
            r = client.get(old, follow_redirects=False)
            assert r.status_code == 307, old
            assert r.headers["location"] == new, old

    def test_nav_splits_reader_and_admin(self, client, db_session):
        reader = client.get("/").text
        admin = client.get("/admin").text
        assert "管理后台" in reader and "渠道" not in reader.split("管理后台")[0]
        assert "概览" in admin and "渠道" in admin and "领域" in admin


class TestReadingApi:
    def test_read_favorite_dismiss(self, client, db_session):
        d = _doc(db_session)
        ok = client.post(f"/api/docs/{d.id}/reading",
                         json={"is_read": True, "is_favorite": True,
                               "rating": 4}).json()["ok"]
        assert ok
        reading = db_session.get(Reading, d.id)
        assert reading.is_read == 1 and reading.is_favorite == 1 and reading.rating == 4
        # 页面表单动作也可用
        r = client.post(f"/docs/{d.id}/dismiss")
        assert r.status_code == 200   # 303 → 自动跟随到 /
        assert db_session.get(Reading, d.id).is_dismissed == 1

    def test_rating_validation(self, client, db_session):
        d = _doc(db_session)
        r = client.post(f"/api/docs/{d.id}/reading", json={"rating": 9})
        assert r.status_code == 400


class TestApi:
    def test_docs_list_and_detail(self, client, db_session):
        d = _doc(db_session, stars=5, kind_tag="tech")
        body = client.get("/api/docs").json()
        assert body["total"] == 1
        assert body["docs"][0]["stars"] == 5
        detail = client.get(f"/api/docs/{d.id}").json()
        assert detail["kind"] == "article" and "正文内容" in detail["content_text"]

    def test_stats(self, client, db_session):
        _doc(db_session)
        body = client.get("/api/stats").json()
        assert body["docs"] == 1 and body["by_kind"]["article"] == 1
        assert body["orphan_docs"] == 0

    def test_analyze_requires_key(self, client, db_session):
        body = client.post("/api/analyze/run").json()
        assert body["ok"] is False and "DEEPSEEK" in body["error"]

    def test_runs_api(self, client, db_session):
        db_session.add(RunLog(kind="analyze", status="done", total=10,
                              processed=10, succeeded=9, failed=1,
                              created_at=NOW, finished_at=NOW))
        db_session.commit()
        assert client.get("/api/runs?kind=analyze").json()["runs"][0]["total"] == 10

    def test_save_url_api_github(self, client, db_session):
        body = client.post("/api/docs/save-url",
                           json={"url": "https://github.com/o/p"}).json()
        assert body["kind"] == "repo" and body["created"]

    def test_fulltext_run_no_overlap(self, client, db_session, monkeypatch):
        db_session.add(RunLog(kind="fulltext", status="running", total=5,
                              created_at=NOW))
        db_session.commit()
        body = client.post("/api/fulltext/run").json()
        assert body["ok"] is False

    def test_fulltext_run_starts_thread(self, client, db_session, monkeypatch):
        started = {}

        def fake_backfill(*a, **kw):
            started["called"] = True

        monkeypatch.setattr("app.services.fulltext_backfill.run_backfill",
                            fake_backfill)
        body = client.post("/api/fulltext/run?limit=5").json()
        assert body["ok"] is True
        import time as _t
        deadline = _t.time() + 2
        while not started.get("called") and _t.time() < deadline:
            _t.sleep(0.05)
        assert started["called"]
