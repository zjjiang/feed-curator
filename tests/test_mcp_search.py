"""MCP search_docs / get_doc 工具测试(直接调函数,索引走 tmp_path)。"""

import json
import time

import pytest

import app.mcp_server as mcp_mod
from app.models import Analysis, Doc

NOW = int(time.time())


@pytest.fixture()
def mcp_db(db_session, monkeypatch, tmp_path):
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=db_session.get_bind(),
                           autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(mcp_mod, "SessionLocal", factory)
    monkeypatch.setattr(mcp_mod, "SEARCH_INDEX_DIR", str(tmp_path / "idx"))
    return db_session


def _mk_doc(db, *, kind="article", url, title, detail, external_id):
    from app.writer import upsert_doc

    return upsert_doc(db, kind=kind, url=url, title=title, detail=detail,
                      pipe_id=1, external_id=external_id, sort_time=NOW)


class TestMcpSearchDocs:
    def test_hit_returns_results(self, mcp_db):
        _mk_doc(mcp_db, url="https://e.com/a1", title="多智能体协作研究",
                detail={"content_text": "多智能体协作框架的任务分配机制。"}, external_id="a1")
        r = mcp_mod.search_docs("多智能体协作")
        assert r["ok"] and r["count"] == 1
        assert r["results"][0]["title"] == "多智能体协作研究"

    def test_blank_query_rejected(self, mcp_db):
        assert not mcp_mod.search_docs("  ")["ok"]

    def test_filters_passthrough(self, mcp_db):
        _mk_doc(mcp_db, url="https://e.com/p1", title="Transformer",
                kind="paper", detail={"abstract": "sequence model"}, external_id="p1")
        r = mcp_mod.search_docs("Transformer sequence", kind="paper")
        assert r["ok"] and r["count"] == 1

    def test_get_doc_returns_content(self, mcp_db):
        res = _mk_doc(mcp_db, url="https://e.com/a2", title="具身智能",
                      detail={"content_text": "具身智能机器人落地。"}, external_id="a2")
        mcp_db.add(Analysis(doc_id=res.doc_id, status="ok", summary="总结",
                            keypoints=json.dumps(["k1"], ensure_ascii=False),
                            domains=json.dumps(["AI"], ensure_ascii=False),
                            stars=4, created_at=NOW))
        mcp_db.commit()
        r = mcp_mod.get_doc(res.doc_id)
        assert r["ok"] and "具身智能" in r["content"]
        assert r["analysis"]["stars"] == 4

    def test_get_doc_missing(self, mcp_db):
        assert not mcp_mod.get_doc(424242)["ok"]
