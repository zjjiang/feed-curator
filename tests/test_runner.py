"""runner.py 判定任务执行器与 main.py 调度循环的测试。

runner/mcp 等模块从 app.db import SessionLocal,这里统一 monkeypatch 成
绑定测试引擎的 sessionmaker,避免触碰真实库。
"""

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_mod
import app.jobs.runner as runner_mod
from app.models import Analysis, Article, Base, Doc, Domain, Pipe, RunLog

NOW = int(time.time())


class FakeLLM:
    model = "fake"

    def __init__(self, sleep=0, fail=False):
        self.sleep = sleep
        self.fail = fail
        self.n = 0

    def analyze(self, **kw):
        self.n += 1
        if self.sleep:
            time.sleep(self.sleep)
        if self.fail:
            return None
        return {"summary": "s", "keypoints": [], "domains": [],
                "article_kind": None, "stars": 3}


@pytest.fixture()
def patched_sessions(monkeypatch, tmp_path):
    """runner 的线程池多线程并发写库,内存库 + StaticPool 单连接会触发
    sqlite3 跨线程误用;这里改用 tmp 文件库(多连接真并发)。"""
    engine = create_engine(
        f"sqlite:///{tmp_path}/runner.db",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(runner_mod, "SessionLocal", factory)
    monkeypatch.setattr(main_mod, "SessionLocal", factory)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def _seed_doc(db, url="https://example.com/a"):
    doc = Doc(kind="article", url_key=url, url=url, title="t",
              first_seen_at=NOW, last_modified_at=NOW)
    db.add(doc)
    db.flush()
    db.add(Article(id=doc.id, content_text="正文" * 100, word_count=600))
    db.commit()
    return doc.id


def _wait_status(db, run_id, want=("done", "failed", "cancelled"), timeout=3.0):
    deadline = time.time() + timeout
    status = "running"
    while time.time() < deadline:
        db.expire_all()
        row = db.get(RunLog, run_id)
        status = row.status
        if status in want:
            return row
        time.sleep(0.05)
    return db.get(RunLog, run_id)


class TestRunner:
    def test_full_run_writes_results(self, patched_sessions, monkeypatch):
        db = patched_sessions
        db.add(Domain(name="D", keywords="[]", created_at=NOW, updated_at=NOW))
        db.commit()
        _seed_doc(db)
        monkeypatch.setattr(main_mod, "_llm_client", FakeLLM())

        run_id, created = runner_mod.start_analyze_job(trigger="manual")
        assert created
        row = _wait_status(db, run_id)
        assert row.status == "done" and row.processed == 1 and row.succeeded == 1
        a = db.query(Analysis).one()
        assert a.stars == 3 and a.status == "ok" and a.model == "fake"

    def test_start_without_docs_returns_none(self, patched_sessions, monkeypatch):
        monkeypatch.setattr(main_mod, "_llm_client", FakeLLM())
        assert runner_mod.start_analyze_job() == (None, False)

    def test_start_without_key_fails_run(self, patched_sessions, monkeypatch):
        db = patched_sessions
        _seed_doc(db)
        monkeypatch.setattr(main_mod, "_llm_client", None)
        run_id, created = runner_mod.start_analyze_job()
        assert created
        row = _wait_status(db, run_id)
        assert row.status == "failed" and "DEEPSEEK" in row.error

    def test_running_job_is_reused(self, patched_sessions, monkeypatch):
        db = patched_sessions
        for i in range(4):
            _seed_doc(db, url=f"https://example.com/{i}")
        monkeypatch.setattr(main_mod, "_llm_client", FakeLLM(sleep=0.15))

        run_id, created = runner_mod.start_analyze_job()
        assert created
        run_id2, created2 = runner_mod.start_analyze_job()
        assert not created2 and run_id2 == run_id
        _wait_status(db, run_id)

    def test_cancel_preserves_completed(self, patched_sessions, monkeypatch):
        db = patched_sessions
        for i in range(8):
            _seed_doc(db, url=f"https://example.com/c{i}")
        monkeypatch.setattr(main_mod, "_llm_client", FakeLLM(sleep=0.5))

        run_id, _ = runner_mod.start_analyze_job()
        time.sleep(0.3)   # 首批 5 个在途,第 6-8 篇尚未开始
        assert runner_mod.cancel_analyze(run_id) is True
        row = _wait_status(db, run_id, timeout=8.0)
        assert row.status == "cancelled", f"status={row.status} error={row.error!r}"
        assert 0 < row.processed < 8
        # 已完成的结果保留
        assert db.query(Analysis).filter_by(status="ok").count() >= row.succeeded


class TestSchedulerCycles:
    def test_fetch_cycle_skips_unsupported_and_disabled(self, patched_sessions,
                                                        monkeypatch):
        db = patched_sessions
        db.add_all([
            Pipe(id=1, type="rss", name="due", config="{}", enabled=1,
                 fetch_interval_min=30, last_fetched_at=NOW - 3600,
                 created_at=NOW, updated_at=NOW),
            Pipe(id=2, type="rss", name="not-due", config="{}", enabled=1,
                 fetch_interval_min=30, last_fetched_at=NOW,
                 created_at=NOW, updated_at=NOW),
            Pipe(id=3, type="rss", name="disabled", config="{}", enabled=0,
                 fetch_interval_min=30, last_fetched_at=NOW - 3600,
                 created_at=NOW, updated_at=NOW),
            Pipe(id=4, type="no-such-type", name="unsupported", config="{}", enabled=1,
                 fetch_interval_min=30, last_fetched_at=NOW - 3600,
                 created_at=NOW, updated_at=NOW),
            Pipe(id=5, type="manual", name="manual", config="{}", enabled=0,
                 fetch_interval_min=30, created_at=NOW, updated_at=NOW),
        ])
        db.commit()

        called = []
        monkeypatch.setattr(main_mod, "fetch_source",
                            lambda db, pipe, trigger="auto": called.append(pipe.id) or (0, None))
        main_mod._run_fetch_cycle()
        assert called == [1]

    def test_fetch_cycle_isolates_pipe_crash(self, patched_sessions, monkeypatch):
        db = patched_sessions
        db.add_all([
            Pipe(id=1, type="rss", name="boom", config="{}", enabled=1,
                 fetch_interval_min=30, last_fetched_at=NOW - 3600,
                 created_at=NOW, updated_at=NOW),
            Pipe(id=2, type="rss", name="fine", config="{}", enabled=1,
                 fetch_interval_min=30, last_fetched_at=NOW - 3600,
                 created_at=NOW, updated_at=NOW),
        ])
        db.commit()

        def flaky(db, pipe, trigger="auto"):
            if pipe.id == 1:
                raise RuntimeError("boom")
            return 1, None
        monkeypatch.setattr(main_mod, "fetch_source", flaky)
        main_mod._run_fetch_cycle()   # 不应抛异常

    def test_analyze_cycle_no_key_noop(self, patched_sessions, monkeypatch):
        called = []
        monkeypatch.setattr(main_mod, "_get_llm", lambda: None)
        monkeypatch.setattr(main_mod, "start_analyze_job",
                            lambda trigger="manual", force_all=False: called.append(trigger))
        main_mod._run_analyze_cycle()
        assert called == []

    def test_analyze_cycle_starts_when_pending(self, patched_sessions, monkeypatch):
        calls = []
        monkeypatch.setattr(main_mod, "_get_llm", lambda: object())
        monkeypatch.setattr(main_mod, "start_analyze_job",
                            lambda trigger="manual", force_all=False:
                            calls.append(trigger) or (1, True))
        main_mod._run_analyze_cycle()
        assert calls == ["auto"]
