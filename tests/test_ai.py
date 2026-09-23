import json
import time

import pytest

from app.ai import analyzer
from app.ai.client import LLMClient
from app.models import Analysis, Article, Domain, Doc, Membership, Paper, Repo

NOW = int(time.time())

DOMAINS = [{"name": "具身智能", "description": "机器人",
            "keywords": ["VLA", "humanoid"]}]


class FakeLLM:
    """替代 LLMClient:返回预设输出,记录调用参数。fails=True 模拟解析失败(返回 None,
    与真实 LLMClient 行为一致——网络/解析错误在 client 内部消化)。"""

    model = "fake-model"

    def __init__(self, payload=None, fails=False):
        self.payload = payload if payload is not None else {
            "summary": "摘要", "keypoints": ["a", "b"],
            "domains": ["具身智能"], "article_kind": "tech", "stars": 4}
        self.fails = fails
        self.calls = []

    def analyze(self, **kwargs):
        self.calls.append(kwargs)
        if self.fails:
            return None
        return dict(self.payload)


def _seed(db, *, kind="article", content="正文" * 300, word_count=1500,
          url="https://example.com/a"):
    doc = Doc(kind=kind, url_key=url, url=url, title="标题",
              first_seen_at=NOW, last_modified_at=NOW)
    db.add(doc)
    db.flush()
    if kind == "article":
        db.add(Article(id=doc.id, content_text=content, word_count=word_count))
    elif kind == "paper":
        db.add(Paper(id=doc.id, abstract=content or None))
    else:
        db.add(Repo(id=doc.id, readme_text=content or None))
    db.commit()
    return doc.id


class TestLLMAnalyzeDefense:
    def setup_method(self):
        self.llm = LLMClient(api_key="k")

    def _run(self, payload, kind="article", sufficient=True):
        return self.llm.analyze(kind=kind, title="t", description="d",
                                content_preview="c",
                                content_sufficient=sufficient, domains=DOMAINS), payload

    def _patch_chat(self, monkeypatch, content):
        monkeypatch.setattr(LLMClient, "chat",
                            lambda self, messages, **kw: content)

    def test_good_output(self, monkeypatch):
        self._patch_chat(monkeypatch, json.dumps({
            "summary": "s", "keypoints": ["k"], "domains": ["具身智能"],
            "article_kind": "tech", "stars": 4}))
        r = self.llm.analyze(kind="article", title="t", description="",
                             content_preview="c", content_sufficient=True,
                             domains=DOMAINS)
        assert r == {"summary": "s", "keypoints": ["k"], "domains": ["具身智能"],
                     "article_kind": "tech", "stars": 4}

    def test_markdown_fences_stripped(self, monkeypatch):
        self._patch_chat(monkeypatch, "```json\n" + json.dumps({
            "summary": "s", "keypoints": [], "domains": [], "stars": 3}) + "\n```")
        r = self.llm.analyze(kind="paper", title="t", description="",
                             content_preview="c", content_sufficient=True,
                             domains=DOMAINS)
        assert r["stars"] == 3 and r["article_kind"] is None

    def test_stars_out_of_range_fails(self, monkeypatch):
        self._patch_chat(monkeypatch, json.dumps({"stars": 9}))
        r = self.llm.analyze(kind="article", title="t", description="",
                             content_preview="c", content_sufficient=True,
                             domains=DOMAINS)
        assert r is None

    def test_unparseable_fails(self, monkeypatch):
        self._patch_chat(monkeypatch, "我觉得这篇文章不错")
        r = self.llm.analyze(kind="article", title="t", description="",
                             content_preview="c", content_sufficient=True,
                             domains=DOMAINS)
        assert r is None

    def test_unknown_domain_dropped(self, monkeypatch):
        self._patch_chat(monkeypatch, json.dumps({
            "summary": "s", "keypoints": [], "domains": ["具身智能", "自创领域"],
            "stars": 3}))
        r = self.llm.analyze(kind="article", title="t", description="",
                             content_preview="c", content_sufficient=True,
                             domains=DOMAINS)
        assert r["domains"] == ["具身智能"]

    def test_invalid_article_kind_cleared_not_failed(self, monkeypatch):
        self._patch_chat(monkeypatch, json.dumps({
            "summary": "s", "keypoints": [], "domains": [],
            "article_kind": "新闻", "stars": 3}))
        r = self.llm.analyze(kind="article", title="t", description="",
                             content_preview="c", content_sufficient=True,
                             domains=DOMAINS)
        assert r["article_kind"] is None and r["stars"] == 3

    def test_short_content_forces_kind_empty(self, monkeypatch):
        self._patch_chat(monkeypatch, json.dumps({
            "summary": "s", "keypoints": [], "domains": ["具身智能"],
            "article_kind": "tech", "stars": 3}))
        r = self.llm.analyze(kind="article", title="t", description="",
                             content_preview="c", content_sufficient=False,
                             domains=DOMAINS)
        # 短正文仍产出摘要/归属/星级,但子类留空
        assert r["summary"] == "s" and r["domains"] == ["具身智能"] and r["stars"] == 3
        assert r["article_kind"] is None

    def test_non_article_forces_kind_empty(self, monkeypatch):
        self._patch_chat(monkeypatch, json.dumps({
            "summary": "s", "keypoints": [], "domains": [],
            "article_kind": "tech", "stars": 5}))
        r = self.llm.analyze(kind="repo", title="t", description="",
                             content_preview="c", content_sufficient=True,
                             domains=DOMAINS)
        assert r["article_kind"] is None


class TestAnalyzerSelection:
    def test_empty_content_skipped(self, db_session):
        _seed(db_session, content="", word_count=0)
        _seed(db_session, url="https://example.com/b")
        ids = analyzer.select_doc_ids(db_session)
        assert len(ids) == 1

    def test_ok_analyzed_not_reselected_failed_retried(self, db_session):
        d1 = _seed(db_session, url="https://example.com/a")
        d2 = _seed(db_session, url="https://example.com/b")
        db_session.add(Analysis(doc_id=d1, status="ok", stars=4, created_at=NOW))
        db_session.add(Analysis(doc_id=d2, status="failed", error="x", created_at=NOW))
        db_session.commit()
        ids = analyzer.select_doc_ids(db_session)
        assert d2 in ids and d1 not in ids

    def test_force_all_includes_analyzed(self, db_session):
        d1 = _seed(db_session)
        db_session.add(Analysis(doc_id=d1, status="ok", stars=4, created_at=NOW))
        db_session.commit()
        assert analyzer.select_doc_ids(db_session, force_all=True) == [d1]


class TestAnalyzeDoc:
    def test_success_writes_analysis_and_materializes(self, db_session):
        d = Domain(name="具身智能", keywords='["VLA"]', created_at=NOW, updated_at=NOW)
        db_session.add(d)
        db_session.flush()
        doc_id = _seed(db_session)
        llm = FakeLLM()

        assert analyzer.analyze_doc(db_session, llm, doc_id, DOMAINS) is True
        a = db_session.query(Analysis).filter_by(doc_id=doc_id).one()
        assert a.status == "ok" and a.stars == 4 and a.model == "fake-model"
        assert json.loads(a.domains) == ["具身智能"]
        m = db_session.query(Membership).filter_by(doc_id=doc_id).one()
        assert m.domain_id == d.id and m.assigned_by == "ai"
        article = db_session.get(Article, doc_id)
        assert article.kind_tag == "tech"

    def test_failure_writes_failed_analysis(self, db_session):
        doc_id = _seed(db_session)
        llm = FakeLLM(fails=True)

        assert analyzer.analyze_doc(db_session, llm, doc_id, DOMAINS) is False
        a = db_session.query(Analysis).filter_by(doc_id=doc_id).one()
        assert a.status == "failed" and a.error

    def test_rejudgment_shrinks_ai_keeps_manual(self, db_session):
        d1 = Domain(name="具身智能", created_at=NOW, updated_at=NOW)
        d2 = Domain(name="另一领域", created_at=NOW, updated_at=NOW)
        db_session.add_all([d1, d2])
        db_session.flush()
        doc_id = _seed(db_session)
        db_session.add(Membership(doc_id=doc_id, domain_id=d2.id,
                                  assigned_by="manual", created_at=NOW))
        db_session.commit()

        # 第一次判入具身智能
        analyzer.analyze_doc(db_session, FakeLLM(), doc_id, DOMAINS)
        # 重新判定:不再属于任何领域
        llm = FakeLLM(payload={"summary": "s", "keypoints": [], "domains": [],
                               "article_kind": "business", "stars": 2})
        analyzer.analyze_doc(db_session, llm, doc_id, DOMAINS)

        rows = db_session.query(Membership).filter_by(doc_id=doc_id).all()
        # ai 记录被移除,manual 保留
        assert [(m.domain_id, m.assigned_by) for m in rows] == [(d2.id, "manual")]
        # analysis 追加不覆盖
        assert db_session.query(Analysis).filter_by(doc_id=doc_id).count() == 2
        assert db_session.get(Article, doc_id).kind_tag == "business"

    def test_paper_input_assembly(self, db_session):
        doc_id = _seed(db_session, kind="paper", content="论文摘要" * 50,
                       url="https://arxiv.org/abs/1")
        llm = FakeLLM(payload={"summary": "s", "keypoints": [], "domains": [],
                               "article_kind": "", "stars": 5})
        analyzer.analyze_doc(db_session, llm, doc_id, DOMAINS)
        call = llm.calls[0]
        assert call["kind"] == "paper"
        assert "论文摘要" in call["content_preview"]
