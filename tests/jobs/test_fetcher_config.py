"""resolve_fetch_config 的 arxiv 派生分支:领域关键词 → 关键词检索串。"""

import json
import time

import pytest

from app.adapters.arxiv import ArxivAdapter
from app.jobs.fetcher import resolve_fetch_config
from app.models import Domain, Pipe

NOW = int(time.time())


def _domain(db_session, keywords: list[str]) -> Domain:
    d = Domain(name=f"域-{hash(tuple(keywords))}", keywords=json.dumps(keywords),
               created_at=NOW, updated_at=NOW)
    db_session.add(d)
    db_session.flush()
    return d


def _derived_pipe(db_session, keywords: list[str]) -> Pipe:
    d = _domain(db_session, keywords)
    p = Pipe(type="arxiv", name="派生", config="{}", domain_id=d.id,
             created_at=NOW, updated_at=NOW)
    db_session.add(p)
    db_session.commit()
    return p


class TestResolveArxivDerived:
    def test_query_generated_from_ascii_keywords(self, db_session):
        p = _derived_pipe(db_session, ["具身智能", "humanoid robot",
                                       "vision-language-action"])
        cfg = resolve_fetch_config(db_session, p)
        assert cfg["query"] == 'all:"humanoid robot" OR all:"vision-language-action"'

    def test_chinese_keywords_filtered_out(self, db_session):
        p = _derived_pipe(db_session, ["具身智能", "Embodied AI"])
        cfg = resolve_fetch_config(db_session, p)
        assert cfg["query"] == 'all:"Embodied AI"'

    def test_query_regenerated_when_keywords_change(self, db_session):
        p = _derived_pipe(db_session, ["Embodied AI"])
        assert resolve_fetch_config(db_session, p)["query"] == 'all:"Embodied AI"'
        db_session.query(Domain).filter(Domain.id == p.domain_id).update({
            Domain.keywords: json.dumps(["Embodied AI", "VLA models"])})
        db_session.commit()
        assert resolve_fetch_config(db_session, p)["query"] == \
            'all:"Embodied AI" OR all:"VLA models"'

    def test_all_chinese_keywords_yield_empty_query(self, db_session):
        p = _derived_pipe(db_session, ["具身智能", "机器人"])
        assert resolve_fetch_config(db_session, p)["query"] == ""

    def test_no_keywords_at_all_yields_empty_query(self, db_session):
        p = _derived_pipe(db_session, [])
        assert resolve_fetch_config(db_session, p)["query"] == ""

    def test_shared_arxiv_pipe_config_untouched(self, db_session):
        p = Pipe(type="arxiv", name="共享", config='{"category": "cs.RO"}',
                 created_at=NOW, updated_at=NOW)
        db_session.add(p)
        db_session.commit()
        assert resolve_fetch_config(db_session, p) == {"category": "cs.RO"}


class TestArxivAdapterShortCircuit:
    def test_empty_query_idles_without_network(self, db_session, monkeypatch):
        def forbidden_request(*args, **kwargs):
            raise AssertionError("空 query 不允许发起网络请求")

        monkeypatch.setattr("app.adapters.arxiv.outbound.request", forbidden_request)
        with pytest.raises(ValueError, match="ASCII 关键词"):
            ArxivAdapter().fetch({"query": "", "category": "cs.AI"})
