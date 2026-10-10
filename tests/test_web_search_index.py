"""/admin 索引状态可见性测试:覆盖卡片、未索引清单、重建路由。"""

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.models import Article, Doc
from app.services import search_index as si


@pytest.fixture()
def client(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(si, "DEFAULT_INDEX_DIR", str(tmp_path / "idx"))
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _doc(db, url, title, content):
    doc = Doc(kind="article", url_key=url, url=url, title=title,
              sort_time=1700000000, first_seen_at=1700000000,
              last_modified_at=1700000000)
    db.add(doc)
    db.flush()
    db.add(Article(id=doc.id, content_text=content, word_count=100))
    db.commit()
    return doc


def test_admin_shows_index_coverage(client, db_session):
    _doc(db_session, "https://e.com/1", "多智能体协作研究", "多智能体协作的任务分配。")
    _doc(db_session, "https://e.com/2", "向量数据库选型", "向量数据库的索引结构。")
    si.build_index(db_session, si.DEFAULT_INDEX_DIR)
    html = client.get("/admin").text
    assert "2/2" in html and "未构建" not in html


def test_admin_shows_uncovered_list(client, db_session):
    _doc(db_session, "https://e.com/1", "多智能体协作研究", "多智能体协作的任务分配。")
    si.build_index(db_session, si.DEFAULT_INDEX_DIR)
    _doc(db_session, "https://e.com/2", "向量数据库选型", "向量数据库的索引结构。")
    html = client.get("/admin").text
    assert "1/2" in html
    assert "未入检索索引" in html and "向量数据库选型" in html


def test_admin_full_coverage_no_list(client, db_session):
    _doc(db_session, "https://e.com/1", "多智能体协作研究", "多智能体协作的任务分配。")
    si.build_index(db_session, si.DEFAULT_INDEX_DIR)
    html = client.get("/admin").text
    assert "1/1" in html and "未入检索索引" not in html


def test_rebuild_route_rebuilds_and_redirects(client, db_session):
    _doc(db_session, "https://e.com/1", "多智能体协作研究", "多智能体协作的任务分配。")
    r = client.post("/admin/search-index/rebuild", follow_redirects=False)
    assert r.status_code == 303 and "msg=index_rebuilt" in r.headers["location"]
    st = si.index_stats(db_session, si.DEFAULT_INDEX_DIR)
    assert st["exists"] and st["indexed"] == 1
    assert "检索索引已全量重建" in client.get("/admin?msg=index_rebuilt").text
