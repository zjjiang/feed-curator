import json

import pytest

from app.adapters.base import FetchedItem
from app.models import Doc, Paper, Repo
from app.services import fulltext, manual_service
from app.services.fulltext import ArchiveError


class FakeGithubClient:
    """按 full_name 返回预置 README;failures 中的抛异常模拟网络失败。"""

    def __init__(self, responses=None, failures=()):
        self.responses = responses or {}
        self.failures = set(failures)
        self.calls = []

    def get_readme(self, owner: str, name: str):
        key = f"{owner}/{name}"
        self.calls.append(key)
        if key in self.failures:
            raise RuntimeError("网络炸了")
        return self.responses.get(key)

    def close(self):
        pass


@pytest.fixture()
def github_factory(monkeypatch):
    """把 manual_service 内部构造的 GitHubClient 替换为可编程 fake。"""

    def install(client: FakeGithubClient) -> FakeGithubClient:
        monkeypatch.setattr("app.services.github_client.GitHubClient",
                            lambda *a, **k: client)
        return client

    return install


class TestSaveUrlNoNetwork:
    """repo / paper 不走网络,直接从 URL 提取结构化字段。"""

    def test_github_url_becomes_repo(self, db_session, github_factory):
        github_factory(FakeGithubClient())
        r = manual_service.save_url(db_session, "https://github.com/acme/cool-repo")
        assert r["kind"] == "repo" and r["created"] and r["error"] is None
        repo = db_session.query(Repo).one()
        assert (repo.owner, repo.name) == ("acme", "cool-repo")

    def test_arxiv_url_becomes_paper(self, db_session, monkeypatch):
        monkeypatch.setattr(manual_service, "fetch_paper_by_id",
                            lambda aid, transport=None: None)
        r = manual_service.save_url(db_session, "https://arxiv.org/abs/2606.02578v1")
        assert r["kind"] == "paper"
        paper = db_session.query(Paper).one()
        assert paper.arxiv_id == "2606.02578"
        assert paper.version == "v1"

    def test_invalid_url_rejected_before_write(self, db_session):
        with pytest.raises(ArchiveError):
            manual_service.save_url(db_session, "not-a-url")
        assert db_session.query(manual_service.Pipe).count() == 1  # manual 管道已建
        from app.models import Doc
        assert db_session.query(Doc).count() == 0

    def test_same_url_twice_not_duplicated(self, db_session, github_factory):
        github_factory(FakeGithubClient())
        manual_service.save_url(db_session, "https://github.com/acme/cool-repo")
        r2 = manual_service.save_url(db_session, "https://github.com/acme/cool-repo/")
        assert not r2["created"]


class TestSaveUrlRepoReadme:
    """手工存入仓库:入库前抓取 README,失败不阻塞。"""

    def test_new_repo_fetches_readme(self, db_session, github_factory):
        client = github_factory(FakeGithubClient({"acme/cool-repo": "# Cool\n正文"}))
        r = manual_service.save_url(db_session, "https://github.com/acme/cool-repo")
        assert r["kind"] == "repo" and r["error"] is None
        repo = db_session.query(Repo).one()
        assert repo.readme_text == "# Cool\n正文"
        assert client.calls == ["acme/cool-repo"]

    def test_readme_404_confirmed_empty(self, db_session, github_factory):
        github_factory(FakeGithubClient())  # 无预置 → get_readme 返回 None(404)
        r = manual_service.save_url(db_session, "https://github.com/acme/x")
        assert r["error"] is None
        repo = db_session.query(Repo).one()
        assert repo.readme_text == ""

    def test_readme_failure_still_saves(self, db_session, github_factory):
        github_factory(FakeGithubClient(failures={"acme/x"}))
        r = manual_service.save_url(db_session, "https://github.com/acme/x")
        assert r["created"] and r["error"]
        repo = db_session.query(Repo).one()
        assert repo.readme_text is None

    def test_filled_readme_not_refetched(self, db_session, github_factory):
        github_factory(FakeGithubClient({"acme/cool-repo": "# 有内容"}))
        manual_service.save_url(db_session, "https://github.com/acme/cool-repo")
        second = github_factory(FakeGithubClient())  # 换一个零调用的 client
        manual_service.save_url(db_session, "https://github.com/acme/cool-repo")
        assert second.calls == []


class TestSaveUrlArticle:
    def test_article_fetches_fulltext(self, db_session, monkeypatch):
        def fake_fetch_and_parse(url, transport=None):
            assert url == "https://example.com/post"
            return {"title": "抓到的标题", "description": "摘要",
                    "author": "作者", "cover_image_url": None,
                    "content_html": "<p>正文</p>", "content_text": "正文",
                    "url": url}

        monkeypatch.setattr(fulltext, "fetch_and_parse", fake_fetch_and_parse)
        r = manual_service.save_url(db_session, "https://example.com/post")
        assert r["kind"] == "article" and r["error"] is None
        from app.models import Article
        article = db_session.query(Article).one()
        assert article.content_text == "正文"

    def test_article_fetch_failure_still_saves(self, db_session, monkeypatch):
        def boom(url, transport=None):
            raise ArchiveError("网页抓取失败:超时")

        monkeypatch.setattr(fulltext, "fetch_and_parse", boom)
        r = manual_service.save_url(db_session, "https://example.com/post")
        assert r["created"] and r["error"] == "网页抓取失败:超时"
        from app.models import Article, Doc
        doc = db_session.query(Doc).one()
        assert doc.title == "https://example.com/post"
        assert db_session.query(Article).count() == 1


def _paper_item() -> FetchedItem:
    return FetchedItem(
        external_id="http://arxiv.org/abs/2606.02578v1",
        title="VLA Survey",
        url="https://arxiv.org/abs/2606.02578v1",
        author="张三, Li Si",
        description="摘要前 500 字",
        content_text="完整摘要",
        content_html=None,
        cover_image_url=None,
        published_at=1780000000,
        meta={"categories": ["cs.RO", "cs.AI"],
              "pdf_url": "http://arxiv.org/pdf/2606.02578v1",
              "all_authors": ["张三", "Li Si"]},
    )


@pytest.fixture()
def paper_fetch(monkeypatch):
    """替换 manual_service 的单篇抓取;calls 记录入参,可断言未发起抓取。"""
    holder = {"calls": []}

    def install(item=None, error=None):
        holder["calls"].clear()

        def fake(arxiv_id, transport=None):
            holder["calls"].append(arxiv_id)
            if error is not None:
                raise error
            return item
        monkeypatch.setattr(manual_service, "fetch_paper_by_id", fake)
        return holder

    return install


class TestSaveUrlPaper:
    """手工存入论文:入库前经 arXiv API 补全实体字段,并按规范身份归并。"""

    def test_new_paper_fetches_abstract(self, db_session, paper_fetch):
        paper_fetch(item=_paper_item())
        r = manual_service.save_url(db_session, "https://arxiv.org/abs/2606.02578v1")
        assert r["kind"] == "paper" and r["error"] is None
        paper = db_session.query(Paper).one()
        assert paper.abstract == "摘要前 500 字"
        assert paper.content_text == "完整摘要"
        assert paper.arxiv_id == "2606.02578" and paper.version == "v1"
        assert json.loads(paper.authors) == ["张三", "Li Si"]
        assert json.loads(paper.categories) == ["cs.RO", "cs.AI"]
        assert paper.pdf_url == "http://arxiv.org/pdf/2606.02578v1"
        assert paper.submitted_at == 1780000000

    def test_paper_fetch_failure_still_saves(self, db_session, paper_fetch):
        paper_fetch(error=RuntimeError("网络炸了"))
        r = manual_service.save_url(db_session, "https://arxiv.org/abs/2606.02578")
        assert r["created"] and r["error"]
        assert db_session.query(Paper).one().abstract is None

    def test_paper_api_empty_still_saves(self, db_session, paper_fetch):
        paper_fetch(item=None)
        r = manual_service.save_url(db_session, "https://arxiv.org/abs/2606.02578")
        assert r["created"] and r["error"]
        assert db_session.query(Paper).one().abstract is None

    def test_abstract_present_not_refetched(self, db_session, paper_fetch):
        paper_fetch(item=_paper_item())
        manual_service.save_url(db_session, "https://arxiv.org/abs/2606.02578v1")
        holder = paper_fetch(item=_paper_item())
        r2 = manual_service.save_url(db_session, "https://arxiv.org/abs/2606.02578v2")
        assert holder["calls"] == []
        assert not r2["created"]

    def test_pdf_url_canonicalizes_and_merges(self, db_session, paper_fetch):
        paper_fetch(item=_paper_item())
        manual_service.save_url(db_session, "https://arxiv.org/abs/2606.02578v1")
        r2 = manual_service.save_url(db_session, "https://arxiv.org/pdf/2606.02578v2")
        assert not r2["created"]
        assert db_session.query(Doc).count() == 1
        doc = db_session.query(Doc).one()
        assert doc.url == "https://arxiv.org/abs/2606.02578"
