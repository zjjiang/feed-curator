import time

import pytest

from app.models import Article, Doc, RunLog
from app.services.fulltext import ArchiveError
from app.services.fulltext_backfill import FULLTEXT_MIN_WORDS, pending_article_ids, run_backfill

NOW = int(time.time())


def _article(db, *, word_count=10, url="https://example.com/short",
             last_modified_at=None):
    doc = Doc(kind="article", url_key=url, url=url, title="t",
              first_seen_at=NOW,
              last_modified_at=last_modified_at or NOW - 10 * 86400)
    db.add(doc)
    db.flush()
    db.add(Article(id=doc.id, content_text="短正文", word_count=word_count))
    db.commit()
    return doc.id


class TestPendingSelection:
    def test_only_short_articles_selected(self, db_session):
        short = _article(db_session, word_count=100)
        full = _article(db_session, word_count=FULLTEXT_MIN_WORDS + 1,
                        url="https://example.com/full")
        ids = pending_article_ids(db_session)
        assert short in ids and full not in ids

    def test_limit(self, db_session):
        _article(db_session, url="https://example.com/a")
        _article(db_session, url="https://example.com/b")
        assert len(pending_article_ids(db_session, limit=1)) == 1

    def test_recently_attempted_excluded_by_cooldown(self, db_session):
        _article(db_session, url="https://example.com/cooled",
                 last_modified_at=NOW - 86400)      # 1 天前刚抓过
        _article(db_session, url="https://example.com/stale")  # 10 天前
        ids = pending_article_ids(db_session)
        assert ids and db_session.query(Doc).filter(
            Doc.url_key == "https://example.com/stale").one().id in ids
        assert db_session.query(Doc).filter(
            Doc.url_key == "https://example.com/cooled").one().id not in ids


class TestRunBackfill:
    def _seed(self, db):
        return [
            _article(db, url="https://example.com/a"),
            _article(db, url="https://example.com/b"),
            _article(db, url="https://example.com/c"),
        ]

    def test_sufficient_articles_not_fetched_and_failures_kept(self, db_session):
        self._seed(db_session)
        # 第 4 篇正文充足,不应被抓取
        _article(db_session, word_count=FULLTEXT_MIN_WORDS + 5,
                 url="https://example.com/full")

        calls = []

        def fake_fetch(url):
            calls.append(url)
            if url.endswith("/c"):
                raise ArchiveError("付费墙")
            return {"title": "t", "description": "d", "author": "a",
                    "cover_image_url": None, "content_html": "<p>x</p>",
                    "content_text": "补全后的长正文" * 100, "url": url}

        stats = run_backfill(db_session, sleep_seconds=0, fetch=fake_fetch,
                             log=lambda *_: None)
        assert stats == {"total": 3, "succeeded": 2, "failed": 1}
        # 失败的那篇也被尝试过(失败 ≠ 跳过);正文充足的从未被抓取
        assert len(calls) == 3
        assert all(not u.endswith("/full") for u in calls)

        # 失败的保留短正文,成功的不留孤儿
        from app.writer import check_orphans
        assert check_orphans(db_session) == 0

        run = db_session.query(RunLog).one()
        assert run.kind == "fulltext" and run.status == "done"
        assert run.total == 3 and run.succeeded == 2 and run.failed == 1
        assert run.finished_at is not None

    def test_word_count_updated_on_success(self, db_session):
        doc_id = _article(db_session, url="https://example.com/a")

        def fake_fetch(url):
            return {"title": "t", "description": None, "author": None,
                    "cover_image_url": "https://img.example.com/x.jpg",
                    "content_html": "<p>x</p>",
                    "content_text": "补全正文" * 300, "url": url}

        run_backfill(db_session, sleep_seconds=0, fetch=fake_fetch,
                     log=lambda *_: None)
        article = db_session.get(Article, doc_id)
        assert article.word_count >= FULLTEXT_MIN_WORDS
        assert article.cover_image_url == "https://img.example.com/x.jpg"
