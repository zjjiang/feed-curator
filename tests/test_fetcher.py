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
    def test_derived_github_keywords_from_domain(self, db_session):
        d = Domain(name="具身智能", keywords='["具身智能", "humanoid robot"]',
                   created_at=NOW, updated_at=NOW)
        db_session.add(d)
        db_session.flush()
        p = Pipe(type="github", name="派生", config='{"min_stars": 20}',
                 domain_id=d.id, created_at=NOW, updated_at=NOW)
        db_session.add(p)
        db_session.commit()

        cfg = resolve_fetch_config(db_session, p)
        assert cfg["keywords"] == ["具身智能", "humanoid robot"]
        assert cfg["min_stars"] == 20

        # 领域新增关键词后,下次采集即用新关键词
        db_session.query(Domain).filter(Domain.id == d.id).update({
            Domain.keywords: '["具身智能", "humanoid robot", "VLA"]'})
        db_session.commit()
        assert resolve_fetch_config(db_session, p)["keywords"][-1] == "VLA"

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


GH_ROWS = [
    {
        "full_name": "acme/vla-robot",
        "html_url": "https://github.com/acme/vla-robot",
        "description": "A VLA model",
        "stargazers_count": 1234,
        "forks_count": 56,
        "open_issues_count": 7,
        "language": "Python",
        "topics": ["vla", "robotics"],
        "license": {"spdx_id": "MIT"},
        "pushed_at": "2026-09-20T08:00:00Z",
    },
    {
        "full_name": "acme/manipulation",
        "html_url": "https://github.com/acme/manipulation",
        "description": "Manipulation toolkit",
        "stargazers_count": 300,
        "forks_count": 20,
        "open_issues_count": 3,
        "language": "C++",
        "topics": [],
        "license": None,
        "pushed_at": "2026-09-19T08:00:00Z",
    },
]


def _patch_github_client(monkeypatch, rows):
    from datetime import UTC, datetime

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def search_repositories(self, query, *, per_page=30):
            return rows

    captured = {}
    monkeypatch.setattr(
        "app.adapters.github.GitHubClient", lambda: FakeClient())
    return int(datetime(2026, 9, 20, 8, 0, 0, tzinfo=UTC).timestamp())


class TestGithubPipe:
    def _derived_pipe(self, db_session):
        d = Domain(name="具身智能", keywords='["具身智能", "VLA"]',
                   created_at=NOW, updated_at=NOW)
        db_session.add(d)
        db_session.flush()
        p = Pipe(type="github", name="具身智能GitHub", config="{}",
                 domain_id=d.id, created_at=NOW, updated_at=NOW)
        db_session.add(p)
        db_session.commit()
        return p

    def test_github_pipe_ingests_repo_entities(self, db_session, monkeypatch):
        pushed_ts = _patch_github_client(monkeypatch, GH_ROWS)
        pipe = self._derived_pipe(db_session)

        inserted, err = fetch_source(db_session, pipe, trigger="manual")
        assert err is None
        assert inserted == 2

        repos = {r.name: r for r in db_session.query(Repo).all()}
        assert set(repos) == {"vla-robot", "manipulation"}
        vla = repos["vla-robot"]
        assert vla.owner == "acme"
        assert vla.stars == 1234
        assert vla.forks == 56
        assert vla.open_issues == 7
        assert vla.language == "Python"
        assert vla.topics == '["vla", "robotics"]'
        assert vla.license == "MIT"
        doc = db_session.query(Doc).filter(Doc.id == vla.id).one()
        assert doc.kind == "repo"
        assert doc.sort_time == pushed_ts

        log = db_session.query(RunLog).one()
        assert log.kind == "fetch" and log.inserted == 2 and log.status == "done"

    def test_github_refetch_is_noop(self, db_session, monkeypatch):
        _patch_github_client(monkeypatch, GH_ROWS)
        pipe = self._derived_pipe(db_session)
        fetch_source(db_session, pipe, trigger="manual")
        inserted, err = fetch_source(db_session, pipe, trigger="manual")
        assert inserted == 0 and err is None
        assert db_session.query(Doc).count() == 2

    def test_repo_already_seen_via_rss_not_duplicated(self, db_session, pipe,
                                                      monkeypatch):
        # RSS 先采到同一仓库(仅标题+URL),github 管道再采只补 discovery
        db_session.query(Pipe).filter(Pipe.id == pipe.id) \
            .update({Pipe.config: json.dumps({"feed_url": FEED_XML})})
        db_session.commit()
        fetch_source(db_session, pipe, trigger="manual")

        _patch_github_client(monkeypatch, [
            {**GH_ROWS[0], "full_name": "acme/robot-arm",
             "html_url": "https://github.com/acme/robot-arm"}])
        gh = Pipe(type="github", name="GH", config='{"query": "VLA stars:>=50"}',
                  created_at=NOW, updated_at=NOW)
        db_session.add(gh)
        db_session.commit()

        inserted, err = fetch_source(db_session, gh, trigger="manual")
        # 不产生新文档,但 github 管道自身的 discovery 记一条(inserted 计 discovery)
        assert err is None and inserted == 1
        assert db_session.query(Doc).count() == 3
        assert db_session.query(Repo).count() == 1
        gh_disc = db_session.query(Discovery).filter(Discovery.pipe_id == gh.id).all()
        assert len(gh_disc) == 1


class TestPaperIdentityMerge:
    """论文身份规范化:多源多形态 arXiv 链接归并为单 doc、各留一条 discovery。"""

    def _rss_pipe(self, db_session, name):
        p = Pipe(type="rss", name=name, config="{}", created_at=NOW, updated_at=NOW)
        db_session.add(p)
        db_session.commit()
        return p

    def _fake_adapter(self, monkeypatch, items):
        from app.adapters.base import SourceAdapter

        class FakeAdapter(SourceAdapter):
            type = "rss"

            def fetch(self, config):
                return items

        monkeypatch.setattr("app.jobs.fetcher.get_adapter", lambda t: FakeAdapter())

    def test_hf_entry_then_hn_pdf_merge_to_single_doc(self, db_session, monkeypatch):
        # HF 策展层先到:用论文 id 构造 canonical abs,缺 categories
        hf = self._rss_pipe(db_session, "HF")
        self._fake_adapter(monkeypatch, [
            FetchedItem(external_id="2606.02578", title="VLA 综述",
                        url="https://arxiv.org/abs/2606.02578",
                        description="摘要", meta={}),
        ])
        inserted1, err1 = fetch_source(db_session, hf, trigger="manual")
        assert err1 is None and inserted1 == 1

        # HN 风格后到:pdf 链接带版本号
        hn = self._rss_pipe(db_session, "HN")
        self._fake_adapter(monkeypatch, [
            FetchedItem(external_id="https://arxiv.org/pdf/2606.02578v2",
                        title="VLA 综述 [pdf]",
                        url="https://arxiv.org/pdf/2606.02578v2",
                        description=None, meta={}),
        ])
        inserted2, err2 = fetch_source(db_session, hn, trigger="manual")
        assert err2 is None and inserted2 == 1

        assert db_session.query(Doc).count() == 1
        assert db_session.query(Paper).count() == 1
        assert db_session.query(Discovery).count() == 2

        doc = db_session.query(Doc).one()
        assert doc.url == "https://arxiv.org/abs/2606.02578"
        assert doc.url_key == doc.url
        paper = db_session.query(Paper).one()
        assert paper.arxiv_id == "2606.02578"
        assert paper.version == "v2"  # version 解析自原始到达 URL
        assert paper.abstract == "摘要"  # 非空不覆盖

    def test_versioned_abs_does_not_duplicate_doc(self, db_session, monkeypatch):
        pipe = self._rss_pipe(db_session, "RSS")
        self._fake_adapter(monkeypatch, [
            FetchedItem(external_id="a1", title="无版本先到",
                        url="https://arxiv.org/abs/2606.02578", meta={}),
        ])
        fetch_source(db_session, pipe, trigger="manual")

        pipe2 = self._rss_pipe(db_session, "arXiv-API")
        self._fake_adapter(monkeypatch, [
            FetchedItem(external_id="a2", title="带版本后到",
                        url="https://arxiv.org/abs/2606.02578v1",
                        meta={"categories": ["cs.AI"]}),
        ])
        inserted, err = fetch_source(db_session, pipe2, trigger="manual")
        assert err is None and inserted == 1
        assert db_session.query(Doc).count() == 1
        assert db_session.query(Discovery).count() == 2
        # 空字段被补全
        paper = db_session.query(Paper).one()
        assert paper.categories == '["cs.AI"]'


class _FakeReadmeClient:
    """同一假客户端同时应付搜索与 README:full_name → readme 正文。"""

    def __init__(self, readmes=None, failures=()):
        self.readmes = readmes or {}
        self.failures = set(failures)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search_repositories(self, query, *, per_page=30):
        return GH_ROWS

    def get_readme(self, owner, name):
        key = f"{owner}/{name}"
        if key in self.failures:
            raise RuntimeError("网络炸了")
        return self.readmes.get(key)

    def close(self):
        pass


class TestGithubReadmeEnrichment:
    def _derived_pipe(self, db_session):
        d = Domain(name="具身智能", keywords='["VLA"]',
                   created_at=NOW, updated_at=NOW)
        db_session.add(d)
        db_session.flush()
        p = Pipe(type="github", name="具身智能GitHub", config="{}",
                 domain_id=d.id, created_at=NOW, updated_at=NOW)
        db_session.add(p)
        db_session.commit()
        return p

    def test_new_repo_readme_filled_in_same_cycle(self, db_session, monkeypatch):
        fake = _FakeReadmeClient({"acme/vla-robot": "# VLA\n说明",
                                  "acme/manipulation": "# Manip"})
        monkeypatch.setattr("app.adapters.github.GitHubClient", lambda: fake)
        monkeypatch.setattr("app.services.repo_enrich.GitHubClient", lambda: fake)
        pipe = self._derived_pipe(db_session)

        inserted, err = fetch_source(db_session, pipe, trigger="manual")
        assert err is None and inserted == 2

        readmes = {r.name: r.readme_text for r in db_session.query(Repo).all()}
        assert readmes == {"vla-robot": "# VLA\n说明", "manipulation": "# Manip"}
        # 补全不产生额外采集记录
        assert db_session.query(RunLog).count() == 1

    def test_readme_failure_keeps_doc_ingested(self, db_session, monkeypatch):
        fake = _FakeReadmeClient(failures={"acme/vla-robot", "acme/manipulation"})
        monkeypatch.setattr("app.adapters.github.GitHubClient", lambda: fake)
        monkeypatch.setattr("app.services.repo_enrich.GitHubClient", lambda: fake)
        pipe = self._derived_pipe(db_session)

        inserted, err = fetch_source(db_session, pipe, trigger="manual")
        assert err is None and inserted == 2
        assert all(r.readme_text is None for r in db_session.query(Repo).all())

    def test_refetch_does_not_refetch_readme(self, db_session, monkeypatch):
        fake = _FakeReadmeClient({"acme/vla-robot": "# VLA\n说明",
                                  "acme/manipulation": "# Manip"})
        monkeypatch.setattr("app.adapters.github.GitHubClient", lambda: fake)
        monkeypatch.setattr("app.services.repo_enrich.GitHubClient", lambda: fake)
        pipe = self._derived_pipe(db_session)
        fetch_source(db_session, pipe, trigger="manual")

        fake.readmes = {}  # 第二轮即使 README 抓不到了,已入库的也不应重抓
        inserted, err = fetch_source(db_session, pipe, trigger="manual")
        assert err is None
        readmes = {r.name: r.readme_text for r in db_session.query(Repo).all()}
        assert readmes == {"vla-robot": "# VLA\n说明", "manipulation": "# Manip"}
