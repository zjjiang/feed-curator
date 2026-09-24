import time

import pytest
from sqlalchemy.orm import Session

from app.models import Analysis, Article, Discovery, Doc, Paper, Repo
from app.writer import UpsertResult, check_orphans, refresh_repo, upsert_doc

NOW = int(time.time())


def _upsell(db: Session, **kw):
    return upsert_doc(
        db,
        kind=kw.get("kind", "article"),
        url=kw.get("url", "https://example.com/a"),
        title=kw.get("title", "标题"),
        detail=kw.get("detail", {"content_text": "正文"}),
        pipe_id=kw.get("pipe_id", 1),
        external_id=kw.get("external_id", "e1"),
        sort_time=kw.get("sort_time"),
        first_seen_at=kw.get("first_seen_at"),
        last_modified_at=kw.get("last_modified_at"),
    )


class TestUpsertDoc:
    def test_new_doc_creates_doc_entity_discovery(self, db_session):
        r = _upsell(db_session)
        assert isinstance(r, UpsertResult)
        assert r.doc_created and r.discovery_created
        assert db_session.query(Doc).count() == 1
        assert db_session.query(Article).count() == 1
        assert db_session.query(Discovery).count() == 1

    def test_url_key_normalized(self, db_session):
        _upsell(db_session, url="https://EXAMPLE.com/a/?utm_source=x")
        doc = db_session.query(Doc).one()
        assert doc.url_key == "https://example.com/a"

    def test_time_fields_filled(self, db_session):
        r = _upsell(db_session, sort_time=12345)
        doc = db_session.get(Doc, r.doc_id)
        assert doc.sort_time == 12345
        assert doc.first_seen_at > 0 and doc.last_modified_at > 0

    def test_explicit_timestamps_for_migration(self, db_session):
        r = _upsell(db_session, first_seen_at=100, last_modified_at=200)
        doc = db_session.get(Doc, r.doc_id)
        assert doc.first_seen_at == 100
        assert doc.last_modified_at == 200

    def test_same_pipe_repeat_skipped(self, db_session):
        _upsell(db_session)
        r2 = _upsell(db_session)
        assert not r2.doc_created and not r2.discovery_created
        assert db_session.query(Doc).count() == 1
        assert db_session.query(Discovery).count() == 1

    def test_cross_pipe_same_url_one_doc_two_discoveries(self, db_session):
        _upsell(db_session, pipe_id=1, external_id="e1")
        r2 = _upsell(db_session, pipe_id=2, external_id="e9")
        assert not r2.doc_created and r2.discovery_created
        assert db_session.query(Doc).count() == 1
        assert db_session.query(Article).count() == 1
        assert db_session.query(Discovery).count() == 2

    def test_detail_columns_landed_on_entity(self, db_session):
        _upsell(
            db_session,
            kind="paper",
            url="https://arxiv.org/abs/2606.02578",
            detail={"abstract": "摘要", "arxiv_id": "2606.02578"},
        )
        paper = db_session.query(Paper).one()
        assert paper.abstract == "摘要"
        assert paper.arxiv_id == "2606.02578"

    def test_entity_failure_rolls_back_doc(self, db_session, monkeypatch):
        real_flush = Session.flush
        calls = {"n": 0}

        def flaky_flush(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:  # 第一次 flush 是取 doc.id,第二次模拟实体写入失败
                raise IntegrityErrorSim()
            return real_flush(self, *a, **kw)

        monkeypatch.setattr(Session, "flush", flaky_flush)
        with pytest.raises(IntegrityErrorSim):
            _upsell(db_session)
        monkeypatch.undo()

        assert db_session.query(Doc).count() == 0
        assert db_session.query(Article).count() == 0
        assert db_session.query(Discovery).count() == 0
        # 回滚后 session 干净可用
        _upsell(db_session)
        assert db_session.query(Doc).count() == 1


class IntegrityErrorSim(Exception):
    pass


class TestRefreshRepo:
    def _seed_repo(self, db_session) -> int:
        r = upsert_doc(
            db_session,
            kind="repo",
            url="https://github.com/owner/proj",
            title="proj",
            detail={"owner": "owner", "name": "proj", "stars": 10},
            pipe_id=1,
            external_id="1",
            sort_time=1000,
        )
        db_session.add(Analysis(doc_id=r.doc_id, status="ok", stars=4, created_at=NOW))
        db_session.commit()
        return r.doc_id

    def test_refresh_updates_repo_and_doc(self, db_session):
        doc_id = self._seed_repo(db_session)
        refresh_repo(db_session, doc_id, stars=25, pushed_at=9999)
        repo = db_session.get(Repo, doc_id)
        doc = db_session.get(Doc, doc_id)
        assert repo.stars == 25
        assert repo.pushed_at == 9999
        assert repo.refreshed_at == doc.last_modified_at
        assert doc.sort_time == 9999

    def test_refresh_does_not_touch_analysis(self, db_session):
        doc_id = self._seed_repo(db_session)
        before = db_session.query(Analysis).filter_by(doc_id=doc_id).one()
        refresh_repo(db_session, doc_id, stars=25, pushed_at=9999)
        after = db_session.query(Analysis).filter_by(doc_id=doc_id).one()
        assert after.id == before.id
        assert after.stars == 4 and after.summary == before.summary
        assert after.created_at == before.created_at

    def test_refresh_stores_star_delta(self, db_session):
        doc_id = self._seed_repo(db_session)  # 初始 stars=10
        refresh_repo(db_session, doc_id, stars=25,
                     stars_prev=10, stars_gained=15)
        repo = db_session.get(Repo, doc_id)
        assert repo.stars == 25
        assert repo.stars_prev == 10
        assert repo.stars_gained == 15

    def test_refresh_without_delta_params_keeps_null(self, db_session):
        doc_id = self._seed_repo(db_session)
        refresh_repo(db_session, doc_id, stars=25, pushed_at=9999)
        repo = db_session.get(Repo, doc_id)
        assert repo.stars_prev is None
        assert repo.stars_gained is None

    def test_refresh_negative_gain_recorded(self, db_session):
        doc_id = self._seed_repo(db_session)
        refresh_repo(db_session, doc_id, stars=8,
                     stars_prev=10, stars_gained=-2)
        assert db_session.get(Repo, doc_id).stars_gained == -2


class TestCheckOrphans:
    def test_reports_orphan_count(self, db_session):
        ok = Doc(kind="repo", url_key="https://github.com/o/r", url="u", title="t",
                 first_seen_at=NOW, last_modified_at=NOW)
        orphan = Doc(kind="paper", url_key="https://arxiv.org/abs/1", url="u", title="t",
                     first_seen_at=NOW, last_modified_at=NOW)
        db_session.add_all([ok, orphan])
        db_session.flush()
        db_session.add(Repo(id=ok.id))
        db_session.commit()
        assert check_orphans(db_session) == 1

    def test_zero_when_consistent(self, db_session):
        _upsell(db_session)
        assert check_orphans(db_session) == 0
