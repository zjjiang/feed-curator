import json
import time

import pytest

from app.adapters import FetchedItem
from app.jobs.fetcher import fetch_source, resolve_fetch_config
from app.models import Article, Discovery, Doc, Domain, Paper, Pipe, Repo, RunLog

NOW = int(time.time())

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>测试源</title>
<item>
  <title>博客文章</title>
  <link>https://example.com/blog/post-1</link>
  <guid>guid-1</guid>
  <pubDate>Mon, 01 Jun 2026 00:00:00 +0000</pubDate>
  <description>摘要</description>
  <content:encoded><![CDATA[<p>这是一段足够长的正文内容,用于计算词数。</p>]]></content:encoded>
</item>
<item>
  <title>GitHub 项目</title>
  <link>https://github.com/acme/robot-arm</link>
  <guid>guid-2</guid>
</item>
<item>
  <title>arXiv 论文</title>
  <link>https://arxiv.org/abs/2606.02578v2</link>
  <guid>guid-3</guid>
</item>
</channel></rss>"""


@pytest.fixture()
def pipe(db_session):
    p = Pipe(type="rss", name="测试管道", config='{"feed_url": "unused"}',
             created_at=NOW, updated_at=NOW)
    db_session.add(p)
    db_session.commit()
    return p


class TestFetchSource:
    def test_three_entries_land_as_three_kinds(self, db_session, pipe):
        db_session.query(Pipe).filter(Pipe.id == pipe.id) \
            .update({Pipe.config: json.dumps({"feed_url": FEED_XML})})
        db_session.commit()

        inserted, err = fetch_source(db_session, pipe, trigger="manual")
        assert err is None
        assert inserted == 3

        kinds = {d.kind for d in db_session.query(Doc).all()}
        assert kinds == {"article", "repo", "paper"}

        repo = db_session.query(Repo).one()
        assert (repo.owner, repo.name) == ("acme", "robot-arm")
        paper = db_session.query(Paper).one()
        assert paper.arxiv_id == "2606.02578"
        assert paper.version == "v2"
        article = db_session.query(Article).one()
        assert article.word_count > 0

        log = db_session.query(RunLog).one()
        assert log.kind == "fetch" and log.status == "done" and log.inserted == 3

    def test_refetch_is_noop(self, db_session, pipe):
        db_session.query(Pipe).filter(Pipe.id == pipe.id) \
            .update({Pipe.config: json.dumps({"feed_url": FEED_XML})})
        db_session.commit()
        fetch_source(db_session, pipe, trigger="manual")
        inserted2, err2 = fetch_source(db_session, pipe, trigger="manual")
        assert inserted2 == 0 and err2 is None
        assert db_session.query(Doc).count() == 3
        assert db_session.query(Discovery).count() == 3

    def test_adapter_failure_is_isolated(self, db_session, pipe):
        db_session.query(Pipe).filter(Pipe.id == pipe.id) \
            .update({Pipe.type: "no-such-type"})
        db_session.commit()
        inserted, err = fetch_source(db_session, pipe)
        assert inserted == 0 and err is not None
        assert "未知的 source type" in err
        assert pipe.last_error == err
        log = db_session.query(RunLog).one()
        assert log.status == "failed" and log.kind == "fetch"

    def test_item_without_url_recorded_as_error(self, db_session, pipe, monkeypatch):
        from app.adapters import get_adapter as real_get_adapter
        from app.adapters.base import SourceAdapter

        class FakeAdapter(SourceAdapter):
            type = "rss"

            def fetch(self, config):
                return [
                    FetchedItem(external_id="ok-1", title="有链接",
                                url="https://example.com/a"),
                    FetchedItem(external_id="bad-1", title="无链接", url="",
                                published_at=None),
                ]

        monkeypatch.setattr("app.jobs.fetcher.get_adapter", lambda t: FakeAdapter())
        inserted, err = fetch_source(db_session, pipe)
        assert inserted == 1
        assert err is not None and "1 条写入失败" in err
        assert db_session.query(Doc).count() == 1


class TestResolveFetchConfig:
    def test_derived_github_query_from_domain_keywords(self, db_session):
        d = Domain(name="具身智能", keywords='["具身智能", "humanoid robot"]',
                   created_at=NOW, updated_at=NOW)
        db_session.add(d)
        db_session.flush()
        p = Pipe(type="github", name="派生", config='{"min_stars": 20}',
                 domain_id=d.id, created_at=NOW, updated_at=NOW)
        db_session.add(p)
        db_session.commit()

        cfg = resolve_fetch_config(db_session, p)
        assert cfg["query"] == "具身智能 humanoid robot"
        assert cfg["min_stars"] == 20

        # 领域新增关键词后,下次采集即用新查询条件
        db_session.query(Domain).filter(Domain.id == d.id).update({
            Domain.keywords: '["具身智能", "humanoid robot", "VLA"]'})
        db_session.commit()
        assert "VLA" in resolve_fetch_config(db_session, p)["query"]

    def test_shared_pipe_config_untouched(self, db_session):
        p = Pipe(type="rss", name="共享", config='{"feed_url": "https://x/feed"}',
                 created_at=NOW, updated_at=NOW)
        db_session.add(p)
        db_session.commit()
        assert resolve_fetch_config(db_session, p) == {"feed_url": "https://x/feed"}

    def test_derived_pipe_without_keywords_keeps_config(self, db_session):
        d = Domain(name="空领域", keywords="[]", created_at=NOW, updated_at=NOW)
        db_session.add(d)
        db_session.flush()
        p = Pipe(type="github", name="派生", config='{"query": "fallback"}',
                 domain_id=d.id, created_at=NOW, updated_at=NOW)
        db_session.add(p)
        db_session.commit()
        assert resolve_fetch_config(db_session, p)["query"] == "fallback"
