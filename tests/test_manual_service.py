import pytest

from app.models import Paper, Repo
from app.services import fulltext, manual_service
from app.services.fulltext import ArchiveError


class TestSaveUrlNoNetwork:
    """repo / paper 不走网络,直接从 URL 提取结构化字段。"""

    def test_github_url_becomes_repo(self, db_session):
        r = manual_service.save_url(db_session, "https://github.com/acme/cool-repo")
        assert r["kind"] == "repo" and r["created"] and r["error"] is None
        repo = db_session.query(Repo).one()
        assert (repo.owner, repo.name) == ("acme", "cool-repo")

    def test_arxiv_url_becomes_paper(self, db_session):
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

    def test_same_url_twice_not_duplicated(self, db_session):
        manual_service.save_url(db_session, "https://github.com/acme/cool-repo")
        r2 = manual_service.save_url(db_session, "https://github.com/acme/cool-repo/")
        assert not r2["created"]


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
