"""doc_search 服务测试:过滤组合、判定缺失、片段、get_doc。"""

import json
import time

import pytest

from app.models import Analysis, Domain, Doc, Membership
from app.services import doc_search
from app.writer import upsert_doc

NOW = int(time.time())


def _mk_doc(db, *, kind="article", url=None, title="标题", detail=None,
            pipe_id=1, external_id=None, sort_time=NOW):
    url = url or f"https://example.com/{kind}/{external_id or 'x'}"
    if detail is None:
        detail = {"content_text": "正文内容"} if kind == "article" else {}
    return upsert_doc(db, kind=kind, url=url, title=title, detail=detail,
                      pipe_id=pipe_id,
                      external_id=external_id or f"e{pipe_id}-{title[:6]}",
                      sort_time=sort_time)


def _add_analysis(db, doc_id, *, stars=5, domains=None, summary="摘要",
                  keypoints=None, status="ok"):
    db.add(Analysis(
        doc_id=doc_id, status=status, summary=summary,
        keypoints=json.dumps(keypoints or ["要点"], ensure_ascii=False),
        domains=json.dumps(domains or [], ensure_ascii=False),
        stars=stars if status == "ok" else None,
        created_at=int(time.time()),
    ))
    db.commit()


def _add_domain(db, name="AI", doc_id=None):
    d = db.query(Domain).filter(Domain.name == name).first()
    if d is None:
        d = Domain(name=name, description="", keywords="[]", enabled=1,
                   created_at=NOW, updated_at=NOW)
        db.add(d)
        db.commit()
    if doc_id is not None:
        db.add(Membership(doc_id=doc_id, domain_id=d.id,
                          assigned_by="ai", created_at=NOW))
        db.commit()
    return d


@pytest.fixture()
def corpus(db_session):
    """两篇可区分主题的文章 + 一篇论文,判定情况各异。"""
    a1 = _mk_doc(db_session, title="多智能体协作框架综述",
                 detail={"content_text": "本文综述多智能体协作的最新进展,涵盖智能体间通信与任务分配,讨论了协作框架的设计取舍。"})
    a2 = _mk_doc(db_session, title="具身智能落地仓储",
                 url="https://example.com/article/embodied",
                 detail={"content_text": "具身智能机器人在仓储场景规模化落地,协作调度是关键能力。"},
                 sort_time=NOW - 40 * 86400)  # 40 天前
    p1 = _mk_doc(db_session, kind="paper", title="Transformer 结构",
                 url="https://arxiv.org/abs/2601.00001", external_id="p1",
                 detail={"abstract": "We propose the Transformer sequence model."})
    _add_analysis(db_session, a1.doc_id, stars=5, domains=["AI"])
    _add_analysis(db_session, p1.doc_id, stars=None, status="failed")  # 判定失败
    _add_domain(db_session, "AI", doc_id=a1.doc_id)
    return db_session


# ============ search_docs ============


class TestSearchDocs:
    def test_basic_hit_with_snippet_and_meta(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "多智能体协作", index_dir=str(tmp_path / "i"))
        assert r["ok"] and r["count"] >= 1
        top = r["results"][0]
        assert top["kind"] == "article" and top["doc_id"] == 1
        assert "协作" in top["snippet"]
        assert top["stars"] == 5 and top["domains"] == ["AI"]
        assert top["url"].startswith("https://")

    def test_kind_filter_excludes_other_kinds(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "Transformer sequence",
                                   kind="paper", index_dir=str(tmp_path / "i"))
        assert r["ok"]
        assert r["results"] and all(x["kind"] == "paper" for x in r["results"])

    def test_kind_filter_no_hit_returns_empty_ok(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "多智能体协作", kind="paper",
                                   index_dir=str(tmp_path / "i"))
        assert r["ok"] and r["count"] == 0 and "note" in r

    def test_days_filter_excludes_old_docs(self, corpus, tmp_path):
        # 「仓储」只出现在 40 天前的 a2;30 天窗口应排除,90 天窗口应命中
        r = doc_search.search_docs(corpus, "仓储", days=30,
                                   index_dir=str(tmp_path / "i"))
        assert r["ok"] and r["count"] == 0
        r2 = doc_search.search_docs(corpus, "仓储", days=90,
                                    index_dir=str(tmp_path / "i"))
        assert r2["count"] == 1

    def test_domain_filter_membership_only(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "多智能体 OR Transformer",
                                   domain="AI", index_dir=str(tmp_path / "i"))
        # 只有 a1 有 AI membership;命中也不含无归属的论文
        assert all(x["doc_id"] == 1 for x in r["results"])

    def test_failed_analysis_not_excluded_fields_null(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "Transformer sequence",
                                   index_dir=str(tmp_path / "i"))
        top = r["results"][0]
        assert top["doc_id"] == 3
        assert top["stars"] is None and top["domains"] == []

    def test_no_hit_ok_with_note(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "量子纠缠隐形传态",
                                   index_dir=str(tmp_path / "i"))
        assert r["ok"] and r["count"] == 0 and "note" in r

    def test_blank_query_rejected(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "   ", index_dir=str(tmp_path / "i"))
        assert not r["ok"] and "error" in r

    def test_invalid_kind_rejected(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "协作", kind="video",
                                   index_dir=str(tmp_path / "i"))
        assert not r["ok"]

    def test_invalid_days_rejected(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "协作", days=0,
                                   index_dir=str(tmp_path / "i"))
        assert not r["ok"]

    def test_limit_clamped_to_cap(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "协作", limit=999,
                                   index_dir=str(tmp_path / "i"))
        assert r["ok"] and len(r["results"]) <= 50

    def test_sort_time_formatted(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "多智能体", index_dir=str(tmp_path / "i"))
        assert r["results"][0]["sort_time"]  # 可读时间串非空


# ============ snippet ============


class TestSnippet:
    def test_window_around_first_hit(self, corpus, tmp_path):
        r = doc_search.search_docs(corpus, "任务分配", index_dir=str(tmp_path / "i"))
        snip = r["results"][0]["snippet"]
        assert "任务分配" in snip and len(snip) <= 300

    def test_hit_beyond_content_falls_back_to_head(self, corpus, tmp_path):
        # 命中词在标题(索引范围内),片段回退正文开头
        r = doc_search.search_docs(corpus, "综述", index_dir=str(tmp_path / "i"))
        assert r["results"][0]["snippet"]


# ============ get_doc ============


class TestGetDoc:
    def test_article_full_content_and_analysis(self, corpus, tmp_path):
        r = doc_search.get_doc(corpus, 1)
        assert r["ok"] and r["kind"] == "article"
        assert "多智能体协作" in r["content"]
        assert r["analysis"]["stars"] == 5
        assert r["analysis"]["keypoints"] == ["要点"]
        assert not r["content_truncated"]

    def test_paper_uses_abstract(self, corpus, tmp_path):
        r = doc_search.get_doc(corpus, 3)
        assert r["ok"] and "Transformer" in r["content"]

    def test_repo_uses_readme(self, db_session, tmp_path):
        _mk_doc(db_session, kind="repo", title="myrepo",
                url="https://github.com/foo/bar", external_id="r1",
                detail={"owner": "foo", "name": "bar", "description": "描述",
                        "readme_text": "# myrepo\n安装方法见文档。"})
        r = doc_search.get_doc(db_session, 1)
        assert r["ok"] and "安装方法" in r["content"]

    def test_truncation_flagged(self, corpus, tmp_path):
        r = doc_search.get_doc(corpus, 1, max_chars=10)
        assert r["content_truncated"] and len(r["content"]) <= 10
        assert r["content_total_chars"] > 10

    def test_missing_doc_rejected(self, corpus, tmp_path):
        r = doc_search.get_doc(corpus, 99999)
        assert not r["ok"] and "error" in r

    def test_no_analysis_analysis_none(self, db_session, tmp_path):
        _mk_doc(db_session, title="无判定文档",
                detail={"content_text": "内容"})
        r = doc_search.get_doc(db_session, 1)
        assert r["ok"] and r["analysis"] is None
