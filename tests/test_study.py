"""学习工作台:载荷 service、API 契约、页面路由。"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.models import (Analysis, Article, Discovery, Doc, Domain, Membership,
                        Pipe, Reading)
from app.services.study_service import collect_study_docs

NOW = int(time.time())
DAY = 86400


def _doc(db, *, title="标题", stars=None, sort_time=None, url=None):
    """建一篇带正文的文章 doc;stars 非 None 时附一条 ok analysis。"""
    url = url or f"https://example.com/{title}"
    doc = Doc(kind="article", url_key=url, url=url, title=title,
              sort_time=sort_time if sort_time is not None else NOW - 3600,
              first_seen_at=NOW - 2 * DAY, last_modified_at=NOW - 2 * DAY)
    db.add(doc)
    db.flush()
    db.add(Article(id=doc.id, content_text="正文" * 200, word_count=400))
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


class TestCollectStudyDocs:
    def test_window_and_sort(self, db_session):
        """窗口过滤 + 星级降序、同星级时间降序。"""
        _doc(db_session, title="新4星", stars=4)
        _doc(db_session, title="新3星", stars=3)
        _doc(db_session, title="旧文档", stars=5,
             sort_time=NOW - 8 * DAY)
        _doc(db_session, title="新更高时间", stars=4, sort_time=NOW - 60)

        out = collect_study_docs(db_session, days=7)
        titles = [d["title"] for d in out]
        assert "旧文档" not in titles
        assert titles == ["新更高时间", "新4星", "新3星"]

    def test_latest_ok_analysis_wins_no_duplicate(self, db_session):
        """force 重判产生多条 ok 记录时,取最新一条且文档不重复。"""
        doc = _doc(db_session, title="重判文", stars=2)
        db_session.add(Analysis(doc_id=doc.id, status="ok", summary="新摘要",
                                keypoints='[]', domains='[]', stars=5,
                                model="m", prompt_version="v1",
                                created_at=NOW + 10))
        db_session.commit()

        out = collect_study_docs(db_session, days=7)
        assert len(out) == 1
        assert out[0]["stars"] == 5
        assert out[0]["summary"] == "新摘要"

    def test_unjudged_doc_kept_with_zero_stars(self, db_session):
        doc = _doc(db_session, title="未判定")
        out = collect_study_docs(db_session, days=7)
        assert len(out) == 1
        assert out[0]["stars"] == 0
        assert out[0]["summary"] == ""
        assert out[0]["keypoints"] == []

    def test_domain_and_pipe_names_mapped(self, db_session):
        doc = _doc(db_session, title="带归属")
        dom = Domain(name="AI大模型", keywords='["llm"]', enabled=1,
                     created_at=NOW, updated_at=NOW)
        pipe = Pipe(type="rss", name="Hacker News 最佳", config="{}",
                    enabled=1, fetch_interval_min=60,
                    created_at=NOW, updated_at=NOW)
        db_session.add_all([dom, pipe])
        db_session.flush()
        db_session.add(Membership(doc_id=doc.id, domain_id=dom.id,
                                  assigned_by="ai", created_at=NOW))
        db_session.add(Discovery(pipe_id=pipe.id, external_id="x1",
                                 doc_id=doc.id, first_seen_at=NOW))
        db_session.commit()

        out = collect_study_docs(db_session, days=7)
        assert out[0]["domains"] == ["AI大模型"]
        assert out[0]["pipes"] == ["Hacker News 最佳"]

    def test_empty_window_returns_empty(self, db_session):
        assert collect_study_docs(db_session, days=7) == []


class TestStudyApi:
    def test_payload_shape(self, client, db_session):
        _doc(db_session, title="一篇", stars=5)
        body = client.get("/api/study/docs?days=7").json()
        assert body["days"] == 7
        assert body["count"] == 1
        doc = body["docs"][0]
        for key in ("id", "kind", "url", "title", "ts", "stars", "summary",
                    "keypoints", "domains", "pipes", "is_read", "is_favorite"):
            assert key in doc
        assert isinstance(doc["keypoints"], list)

    def test_days_out_of_range_rejected(self, client):
        assert client.get("/api/study/docs?days=0").status_code == 422
        assert client.get("/api/study/docs?days=31").status_code == 422

    def test_reading_api_reflects_in_payload(self, client, db_session):
        """工作台的已读/收藏写操作通过现有 reading API 生效。"""
        doc = _doc(db_session, title="待读")
        resp = client.post(f"/api/docs/{doc.id}/reading",
                           json={"is_read": True, "is_favorite": True})
        assert resp.status_code == 200
        body = client.get("/api/study/docs").json()
        assert body["docs"][0]["is_read"] is True
        assert body["docs"][0]["is_favorite"] is True


class TestStudyPage:
    def test_page_renders(self, client, db_session):
        html = client.get("/study").text
        assert "学习工作台" in html
        assert "/api/study/docs" in html

    def test_layout_has_nav_link(self, client, db_session):
        assert '/study' in client.get("/").text
