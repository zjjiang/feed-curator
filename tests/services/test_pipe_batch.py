"""批量拉取:顺序执行、失败隔离、单飞防重入、空选择拒绝。"""

import pytest

from app.models import Pipe
import app.services.pipe_batch as pb
from app.services.pipe_batch import run_batch_fetch, start_batch_fetch

DAY_AGO = 1759000000


def _pipe(db, name):
    p = Pipe(type="rss", name=name, config="{}", enabled=1,
             fetch_interval_min=30, created_at=DAY_AGO, updated_at=DAY_AGO)
    db.add(p)
    db.flush()
    return p


@pytest.fixture()
def three_pipes(db_session):
    pipes = [_pipe(db_session, f"源{i}") for i in range(3)]
    db_session.commit()
    return pipes


class TestRunBatchFetch:
    def test_fetches_each_selected_in_order(self, db_session, monkeypatch, three_pipes):
        called = []
        monkeypatch.setattr(pb, "fetch_source",
                            lambda db, p, trigger="manual": called.append(p.id) or (1, None))
        monkeypatch.setattr(pb, "SessionLocal", lambda: db_session)

        out = run_batch_fetch([p.id for p in three_pipes])

        assert called == [p.id for p in three_pipes]
        assert out == {"ok": 3, "failed": 0}

    def test_failure_isolated_and_recorded(self, db_session, monkeypatch, three_pipes):
        def fake_fetch(db, p, trigger="manual"):
            if p.name == "源1":
                raise RuntimeError("网络炸了")
            return 1, None

        monkeypatch.setattr(pb, "fetch_source", fake_fetch)
        monkeypatch.setattr(pb, "SessionLocal", lambda: db_session)

        out = run_batch_fetch([p.id for p in three_pipes])

        assert out == {"ok": 2, "failed": 1}
        p1 = db_session.get(Pipe, three_pipes[1].id)
        assert "网络炸了" in (p1.last_error or "")

    def test_missing_pipe_skipped(self, db_session, monkeypatch):
        called = []
        monkeypatch.setattr(pb, "fetch_source",
                            lambda db, p, trigger="manual": called.append(p.id) or (1, None))
        monkeypatch.setattr(pb, "SessionLocal", lambda: db_session)

        out = run_batch_fetch([99999])
        assert out == {"ok": 0, "failed": 0} and called == []


class TestStartBatchFetch:
    def setup_method(self):
        pb._BATCH_LOCK = __import__("threading").Lock()

    def test_empty_selection_rejected(self, db_session, monkeypatch):
        monkeypatch.setattr(pb, "SessionLocal", lambda: db_session)
        assert start_batch_fetch([]) == "empty"

    def test_busy_when_lock_held(self, db_session, monkeypatch):
        monkeypatch.setattr(pb, "SessionLocal", lambda: db_session)
        pb._BATCH_LOCK.acquire()
        try:
            assert start_batch_fetch([1, 2]) == "busy"
        finally:
            pb._BATCH_LOCK.release()

    def test_starts_thread(self, db_session, monkeypatch, three_pipes):
        import threading
        done = threading.Event()
        monkeypatch.setattr(pb, "SessionLocal", lambda: db_session)
        monkeypatch.setattr(pb, "run_batch_fetch",
                            lambda ids: done.set() or {"ok": len(ids), "failed": 0})

        assert start_batch_fetch([p.id for p in three_pipes]) == "started"
        assert done.wait(timeout=5)
