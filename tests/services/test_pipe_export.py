"""订阅源 JSON 导出:内容分类、结构与 roundtrip 字段、git 推送编排。"""

import json
from datetime import datetime

import pytest

from app.models import Domain, Pipe, RunLog
import app.services.pipe_export as pe
from app.services.pipe_export import _category_of, build_pipes_json

NOW = int(datetime(2026, 10, 9, 12, 0).timestamp())
DAY_AGO = NOW - 86400


def _domain(db, name):
    d = Domain(name=name, keywords='["k"]', enabled=1,
               created_at=DAY_AGO, updated_at=DAY_AGO)
    db.add(d)
    db.flush()
    return d


def _pipe(db, type_, name, config, *, domain_id=None, enabled=1,
          last_fetched_at=None):
    p = Pipe(type=type_, name=name, config=json.dumps(config, ensure_ascii=False),
             domain_id=domain_id, enabled=enabled, fetch_interval_min=30,
             last_fetched_at=last_fetched_at,
             created_at=DAY_AGO, updated_at=DAY_AGO)
    db.add(p)
    return p


@pytest.fixture()
def six_types(db_session):
    d = _domain(db_session, "LLM")
    _pipe(db_session, "rss", "少数派", {"feed_url": "https://sspai.com/feed"})
    _pipe(db_session, "arxiv", "cs.AI", {"category": "cs.AI"})
    _pipe(db_session, "github", "gh-LLM", {}, domain_id=d.id)
    _pipe(db_session, "github", "gh-shared", {"query": "stars:>1000 llm"})
    _pipe(db_session, "hf_papers", "HF 每日", {"base_url": "http://localhost:9002"})
    _pipe(db_session, "wechat", "机器之心", {"mp_id": "xyz", "secret": "s3cret"})
    _pipe(db_session, "manual", "手工存入", {"mp_id": "manual"})
    db_session.commit()


# ============ 内容大类 ============


class TestContentCategories:
    def test_paper_sources_not_buried(self, db_session):
        _pipe(db_session, "rss", "HuggingFace 每日论文",
              {"feed_url": "http://127.0.0.1:9002/huggingface/daily-papers"})
        _pipe(db_session, "rss", "BAIR Berkeley",
              {"feed_url": "https://bair.berkeley.edu/blog/feed.xml"})
        db_session.commit()
        by_name = {p["name"]: p["category"]
                   for p in json.loads(build_pipes_json(db_session, now=NOW))["pipes"]}
        assert by_name["HuggingFace 每日论文"] == "论文与研究"
        assert by_name["BAIR Berkeley"] == "论文与研究"

    def test_category_rules_by_order(self, db_session):
        _pipe(db_session, "rss", "OpenAI News", {"feed_url": "https://openai.com/news/rss.xml"})
        _pipe(db_session, "rss", "HuggingFace Blog", {"feed_url": "https://huggingface.co/blog/feed.xml"})
        _pipe(db_session, "rss", "Hacker News 最佳", {"feed_url": "http://127.0.0.1:9002/hackernews/best"})
        _pipe(db_session, "rss", "Simon Willison", {"feed_url": "https://simonwillison.net/atom/everything/"})
        _pipe(db_session, "rss", "36氪", {"feed_url": "http://127.0.0.1:9002/36kr/news/latest"})
        db_session.commit()
        by_name = {p["name"]: p["category"]
                   for p in json.loads(build_pipes_json(db_session, now=NOW))["pipes"]}
        assert by_name["OpenAI News"] == "厂商官方"
        assert by_name["HuggingFace Blog"] == "厂商官方"
        assert by_name["Hacker News 最佳"] == "开发者与独立博客"
        assert by_name["Simon Willison"] == "开发者与独立博客"
        assert by_name["36氪"] == "科技媒体"  # rss 兜底

    def test_type_shortcuts(self, db_session, six_types):
        """arxiv/hf_papers→论文,github→开发者,wechat/manual→各自大类。"""
        by_name = {p["name"]: p["category"]
                   for p in json.loads(build_pipes_json(db_session, now=NOW))["pipes"]}
        assert by_name["cs.AI"] == "论文与研究"
        assert by_name["HF 每日"] == "论文与研究"
        assert by_name["gh-LLM"] == "开发者与独立博客"
        assert by_name["机器之心"] == "微信公众号"
        assert by_name["手工存入"] == "手工存入"
        assert by_name["少数派"] == "科技媒体"

    def test_export_category_overrides_rules(self, db_session):
        _pipe(db_session, "rss", "自定义源",
              {"feed_url": "https://x.com/f", "export_category": "论文与研究"})
        db_session.commit()
        data = json.loads(build_pipes_json(db_session, now=NOW))
        assert data["pipes"][0]["category"] == "论文与研究"

    def test_arxiv_category_field_is_not_override(self, db_session):
        """arxiv 管道自带的 category 字段是 arXiv 分类,不参与覆盖。"""
        p = _pipe(db_session, "arxiv", "cs.CV", {"category": "cs.CV"})
        db_session.commit()
        assert _category_of(p) == "论文与研究"

    def test_research_blogs_in_paper_group(self, db_session):
        for name in ("Google Research", "DeepMind", "Microsoft Research"):
            _pipe(db_session, "rss", name, {"feed_url": f"https://{name}.com/rss"})
        db_session.commit()
        cats = {p["name"]: p["category"]
                for p in json.loads(build_pipes_json(db_session, now=NOW))["pipes"]}
        assert all(cats[n] == "论文与研究"
                   for n in ("Google Research", "DeepMind", "Microsoft Research"))


# ============ pipes.json 构建 ============


class TestBuildPipesJson:
    def test_structure_and_roundtrip_fields(self, db_session, six_types):
        data = json.loads(build_pipes_json(db_session, now=NOW))
        assert data["version"] == 1 and data["exported_at"] == NOW
        assert len(data["pipes"]) == 7
        gh_derived = next(p for p in data["pipes"] if p["name"] == "gh-LLM")
        assert gh_derived["domain"] == "LLM"          # 名字而非 id
        assert gh_derived["type"] == "github"
        wx = next(p for p in data["pipes"] if p["type"] == "wechat")
        assert wx["config"]["mp_id"] == "xyz"          # 完整 config
        assert set(data["pipes"][0]) == {"type", "name", "config", "domain",
                                         "enabled", "fetch_interval_min",
                                         "category"}

    def test_empty_db(self, db_session):
        data = json.loads(build_pipes_json(db_session, now=NOW))
        assert data["pipes"] == []


# ============ git 推送编排 ============


class _R:
    def __init__(self, code, out=""):
        self.returncode = code
        self.stdout = out
        self.stderr = ""


class TestRunExport:
    def _monkey_git(self, monkeypatch, tmp_path, branch="main", staged_change=1,
                    calls=None):
        def fake_run(args, **kw):
            cmd = " ".join(args)
            if calls is not None:
                calls.append(cmd)
            if "rev-parse" in cmd and "--abbrev-ref" in cmd:
                return _R(0, branch + "\n")
            if "--cached" in cmd and "--quiet" in cmd:
                return _R(staged_change)
            return _R(0)

        monkeypatch.setattr(pe.subprocess, "run", fake_run)
        monkeypatch.setattr(pe, "REPO_ROOT", tmp_path)

    def test_happy_path_commits_and_pushes(self, db_session, monkeypatch, tmp_path):
        calls = []
        self._monkey_git(monkeypatch, tmp_path, calls=calls)
        _pipe(db_session, "rss", "源A", {"feed_url": "https://a.com/f"})
        db_session.commit()

        pe.run_export(db_session, trigger="manual")

        assert any("commit" in c for c in calls)
        assert any(c.startswith("git push") for c in calls)
        content = (tmp_path / "docs" / "pipes.json").read_text()
        assert "源A" in content
        log = db_session.query(RunLog).filter(RunLog.kind == "export").one()
        assert log.status == "done"

    def test_no_change_skips_commit(self, db_session, monkeypatch, tmp_path):
        calls = []
        self._monkey_git(monkeypatch, tmp_path, staged_change=0, calls=calls)
        pe.run_export(db_session, trigger="manual")
        assert not any("commit" in c for c in calls)
        assert not any("push" in c for c in calls)
        log = db_session.query(RunLog).filter(RunLog.kind == "export").one()
        assert log.status == "done"

    def test_non_main_branch_skips(self, db_session, monkeypatch, tmp_path):
        calls = []
        self._monkey_git(monkeypatch, tmp_path, branch="feat/x", calls=calls)
        pe.run_export(db_session, trigger="auto")
        assert not any("push" in c for c in calls)
        log = db_session.query(RunLog).filter(RunLog.kind == "export").one()
        assert log.status == "cancelled"
        assert "main" in (log.error or "")

    def test_push_failure_recorded(self, db_session, monkeypatch, tmp_path):
        def fake_run(args, **kw):
            cmd = " ".join(args)
            if "rev-parse" in cmd:
                return _R(0, "main\n")
            if "--cached" in cmd and "--quiet" in cmd:
                return _R(1)
            if "push" in cmd:
                return _R(128)  # 两次 push 都失败
            return _R(0)

        monkeypatch.setattr(pe.subprocess, "run", fake_run)
        monkeypatch.setattr(pe, "REPO_ROOT", tmp_path)
        pe.run_export(db_session, trigger="manual")
        log = db_session.query(RunLog).filter(RunLog.kind == "export").one()
        assert log.status == "failed"
        assert log.error

    def test_maybe_start_export_auto_due_check(self, db_session, monkeypatch):
        """auto:24h 内已成功导出则不启动。"""
        monkeypatch.setattr(pe, "SessionLocal", lambda: db_session)
        db_session.add(RunLog(kind="export", trigger="auto", status="done",
                              total=1, processed=1, created_at=NOW - 3600))
        db_session.commit()
        monkeypatch.setattr(pe.time, "time", lambda: NOW)
        assert pe.export_due() is False

    def test_maybe_start_export_manual_ignores_due(self, db_session, monkeypatch):
        """manual 无视到期直接启动(线程体 no-op,等线程真正跑完再收尾)。"""
        import threading

        monkeypatch.setattr(pe, "SessionLocal", lambda: db_session)
        started = threading.Event()

        def fake_run(*a, **k):
            started.set()

        monkeypatch.setattr(pe, "run_export", fake_run)
        db_session.add(RunLog(kind="export", trigger="auto", status="done",
                              total=1, processed=1, created_at=NOW - 3600))
        db_session.commit()
        monkeypatch.setattr(pe.time, "time", lambda: NOW)

        assert pe.maybe_start_export(trigger="manual") is True
        assert started.wait(timeout=5)
        # 等后台线程释放锁,再验证 auto 被 due 拦截
        import time as _t
        deadline = _t.time() + 5
        while pe._EXPORT_LOCK.locked() and _t.time() < deadline:
            _t.sleep(0.01)
        assert pe.maybe_start_export(trigger="auto") is False

    def test_maybe_start_export_busy(self, db_session, monkeypatch):
        import threading

        monkeypatch.setattr(pe, "SessionLocal", lambda: db_session)
        pe._EXPORT_LOCK.acquire()
        try:
            assert pe.maybe_start_export(trigger="manual") is False
        finally:
            pe._EXPORT_LOCK.release()
