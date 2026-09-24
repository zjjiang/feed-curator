"""星标刷新作业的单元测试:增量计算/判定隔离/轮转降级/限流停轮/失败隔离。"""

import time
from datetime import UTC, datetime

from app.models import Analysis, Doc, Membership, Domain, Reading, Repo, RunLog
from app.services.github_client import RateLimitError
from app.services.repo_refresh import (
    DEFAULT_BATCH_LIMIT,
    refresh_due,
    repo_ids_for_refresh,
    run_refresh,
)

NOW = int(time.time())
PUSHED_TS = int(datetime(2026, 5, 15, 10, 0, 0, tzinfo=UTC).timestamp())


class FakeGH:
    """full_name → 指标响应;failures 抛通用异常,rate_limit_on 抛限流。"""

    def __init__(self, metrics=None, failures=(), rate_limit_on=()):
        self.metrics = metrics or {}
        self.failures = set(failures)
        self.rate_limit_on = set(rate_limit_on)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_repo(self, owner, name):
        key = f"{owner}/{name}"
        self.calls.append(key)
        if key in self.rate_limit_on:
            raise RateLimitError(9999999999)
        if key in self.failures:
            raise RuntimeError("网络炸了")
        stars = self.metrics.get(key, {}).get("stars")
        return {
            "stargazers_count": stars,
            "forks_count": 9,
            "open_issues_count": 4,
            "pushed_at": "2026-05-15T10:00:00Z",
        }

    def close(self):
        pass


def _seed_repo(db, full_name, *, stars=None, refreshed_at=None):
    owner, name = full_name.split("/")
    doc = Doc(kind="repo", url_key=f"https://github.com/{full_name}",
              url=f"https://github.com/{full_name}", title=full_name,
              sort_time=1000, first_seen_at=NOW, last_modified_at=NOW)
    db.add(doc)
    db.flush()
    db.add(Repo(id=doc.id, owner=owner, name=name, stars=stars,
                refreshed_at=refreshed_at))
    db.commit()
    return doc.id


def test_first_refresh_has_no_delta(db_session):
    doc_id = _seed_repo(db_session, "acme/vla", stars=None)
    gh = FakeGH({"acme/vla": {"stars": 120}})

    stats = run_refresh(db_session, client=gh, sleep_seconds=0)

    assert stats["succeeded"] == 1
    repo = db_session.get(Repo, doc_id)
    assert repo.stars == 120
    assert repo.stars_prev is None and repo.stars_gained is None
    assert repo.refreshed_at is not None


def test_refresh_computes_positive_delta(db_session):
    doc_id = _seed_repo(db_session, "acme/vla", stars=100)
    gh = FakeGH({"acme/vla": {"stars": 120}})

    run_refresh(db_session, client=gh, sleep_seconds=0)

    repo = db_session.get(Repo, doc_id)
    assert (repo.stars_prev, repo.stars_gained, repo.stars) == (100, 20, 120)


def test_negative_delta_recorded(db_session):
    doc_id = _seed_repo(db_session, "acme/vla", stars=100)
    gh = FakeGH({"acme/vla": {"stars": 98}})

    run_refresh(db_session, client=gh, sleep_seconds=0)

    assert db_session.get(Repo, doc_id).stars_gained == -2


def test_refresh_updates_doc_sort_time_from_pushed_at(db_session):
    doc_id = _seed_repo(db_session, "acme/vla", stars=100)
    run_refresh(db_session, client=FakeGH({"acme/vla": {"stars": 101}}),
                sleep_seconds=0)
    assert db_session.get(Doc, doc_id).sort_time == PUSHED_TS


def test_refresh_does_not_touch_judgment_data(db_session):
    doc_id = _seed_repo(db_session, "acme/vla", stars=100)
    d = Domain(name="具身智能", created_at=NOW, updated_at=NOW)
    db_session.add(d)
    db_session.flush()
    db_session.add(Analysis(doc_id=doc_id, status="ok", stars=5, summary="s",
                            created_at=NOW))
    db_session.add(Membership(doc_id=doc_id, domain_id=d.id, assigned_by="ai",
                              created_at=NOW))
    db_session.add(Reading(doc_id=doc_id, is_favorite=1, updated_at=NOW))
    db_session.commit()

    run_refresh(db_session, client=FakeGH({"acme/vla": {"stars": 130}}),
                sleep_seconds=0)

    assert db_session.query(Analysis).filter_by(doc_id=doc_id).one().summary == "s"
    assert db_session.query(Membership).filter_by(doc_id=doc_id).count() == 1
    assert db_session.get(Reading, doc_id).is_favorite == 1


def test_run_log_records_refresh_round(db_session):
    _seed_repo(db_session, "acme/vla", stars=100)
    run_refresh(db_session, client=FakeGH({"acme/vla": {"stars": 110}}),
                sleep_seconds=0)
    run = db_session.query(RunLog).filter(RunLog.kind == "refresh").one()
    assert run.status == "done"
    assert run.total == 1 and run.succeeded == 1
    assert run.finished_at is not None
    assert run.pipe_name == "星标刷新"


def test_no_token_rotates_least_recently_refreshed(db_session, monkeypatch):
    monkeypatch.setattr("app.services.repo_refresh._token", lambda: None)
    id_old = _seed_repo(db_session, "acme/old", stars=1)  # 从未刷新,最优先
    id_mid = _seed_repo(db_session, "acme/mid", stars=1, refreshed_at=100)
    id_new = _seed_repo(db_session, "acme/new", stars=1, refreshed_at=200)

    ids = repo_ids_for_refresh(db_session, limit=None)
    assert ids == [id_old, id_mid, id_new]

    stats = run_refresh(db_session, client=FakeGH(), sleep_seconds=0, limit=2)

    assert stats["total"] == 2
    run = db_session.query(RunLog).filter(RunLog.kind == "refresh").one()
    assert run.total == 2
    # 下一轮 acme/new(从未刷新)排最前,轮转可覆盖全部
    remaining = repo_ids_for_refresh(db_session, limit=None)
    assert remaining[0] == id_new


def test_with_token_no_batch_limit(db_session, monkeypatch):
    monkeypatch.setattr("app.services.repo_refresh._token", lambda: "tok")
    _seed_repo(db_session, "acme/a", stars=1)
    _seed_repo(db_session, "acme/b", stars=1)
    _seed_repo(db_session, "acme/c", stars=1)

    stats = run_refresh(db_session, client=FakeGH(), sleep_seconds=0)

    assert stats["total"] == 3


def test_rate_limit_stops_round(db_session):
    id_a = _seed_repo(db_session, "acme/a", stars=1)
    id_b = _seed_repo(db_session, "acme/b", stars=1)
    id_c = _seed_repo(db_session, "acme/c", stars=1)
    gh = FakeGH(rate_limit_on={"acme/b"})

    stats = run_refresh(db_session, client=gh, sleep_seconds=0)

    assert gh.calls == ["acme/a", "acme/b"]  # 限流即停,不再打第三个
    assert stats["succeeded"] == 1 and stats["failed"] == 1
    run = db_session.query(RunLog).filter(RunLog.kind == "refresh").one()
    assert "限流" in (run.error or "")
    assert db_session.get(Repo, id_c).refreshed_at is None


def test_single_failure_isolated(db_session):
    id_a = _seed_repo(db_session, "acme/a", stars=1)
    id_b = _seed_repo(db_session, "acme/b", stars=1)
    gh = FakeGH(failures={"acme/a"}, metrics={"acme/b": {"stars": 5}})

    stats = run_refresh(db_session, client=gh, sleep_seconds=0)

    assert stats["failed"] == 1 and stats["succeeded"] == 1
    assert db_session.get(Repo, id_a).refreshed_at is None
    assert db_session.get(Repo, id_b).stars == 5


def test_missing_owner_name_skipped(db_session):
    doc_id = _seed_repo(db_session, "acme/x", stars=1)
    db_session.get(Repo, doc_id).owner = None
    db_session.commit()
    gh = FakeGH()

    stats = run_refresh(db_session, client=gh, sleep_seconds=0)

    assert stats.get("skipped", 0) == 1
    assert gh.calls == []


class TestRefreshDue:
    def _log(self, db, kind_status, created_at):
        db.add(RunLog(kind="refresh", pipe_name="星标刷新",
                      status=kind_status, created_at=created_at))
        db.commit()

    def test_never_ran_is_due(self, db_session):
        assert refresh_due(db_session)

    def test_recent_done_not_due(self, db_session):
        self._log(db_session, "done", NOW - 3600)
        assert not refresh_due(db_session)

    def test_old_done_is_due(self, db_session):
        self._log(db_session, "done", NOW - 25 * 3600)
        assert refresh_due(db_session)

    def test_running_blocks(self, db_session):
        self._log(db_session, "running", NOW - 25 * 3600)
        assert not refresh_due(db_session)

    def test_interval_overridable(self, db_session):
        self._log(db_session, "done", NOW - 7200)
        assert refresh_due(db_session, interval_seconds=3600)


def test_default_batch_limit_value():
    assert DEFAULT_BATCH_LIMIT == 50
