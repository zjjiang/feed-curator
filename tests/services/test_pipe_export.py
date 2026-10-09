"""订阅源清单导出:md 生成(纯函数)+ git 推送编排。"""

import json
from datetime import datetime

import pytest

from app.models import Domain, Pipe, RunLog
import app.services.pipe_export as pe
from app.services.pipe_export import render_pipes_md

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


# ============ md 生成 ============


class TestRenderPipesMd:
    def test_content_groups(self, db_session, six_types):
        """六根管道按内容大类落组:arxiv/hf→论文,github→社区,rss→媒体。"""
        md = render_pipes_md(db_session, now=NOW)
        assert "cs.AI" in md.split("## 论文与研究")[1].split("##")[0]
        assert "HF 每日" in md.split("## 论文与研究")[1].split("##")[0]
        assert "gh-LLM" in md.split("## 开发者与独立博客")[1].split("##")[0]
        assert "少数派" in md.split("## 科技媒体")[1].split("##")[0]
        assert "机器之心" in md.split("## 微信公众号")[1].split("##")[0]

    def test_header_time_and_total(self, db_session, six_types):
        md = render_pipes_md(db_session, now=NOW)
        assert "2026-10-09" in md and "7" in md  # 7 个源

    def test_row_fields_and_config_summary(self, db_session, six_types):
        md = render_pipes_md(db_session, now=NOW)
        assert "https://sspai.com/feed" in md            # rss → feed_url
        assert "cs.AI" in md                             # arxiv → category
        assert "按领域关键词自动生成" in md                # 派生 github
        assert "stars:>1000 llm" in md                   # 共享 github → query
        assert "http://localhost:9002" in md             # hf_papers
        assert "xyz" in md                               # wechat → mp_id
        assert "s3cret" not in md                        # 其他 config 字段不泄漏
        assert "LLM" in md                               # 派生标注领域

    def test_disabled_pipe_marked(self, db_session):
        _pipe(db_session, "rss", "停用源", {"feed_url": "https://x.com/feed"},
              enabled=0)
        db_session.commit()
        md = render_pipes_md(db_session, now=NOW)
        assert "停用" in md

    def test_empty_types_omitted(self, db_session):
        _pipe(db_session, "rss", "唯一", {"feed_url": "https://a.com/f"})
        db_session.commit()
        md = render_pipes_md(db_session, now=NOW)
        assert "## 科技媒体" in md
        assert "微信公众号" not in md and "手工存入" not in md

    def test_empty_db_renders_header_only(self, db_session):
        md = render_pipes_md(db_session, now=NOW)
        assert "共 0 个源" in md


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
            if "add" in cmd:
                return _R(0)
            if "commit" in cmd:
                return _R(0)
            if "push" in cmd:
                return _R(0)
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
        assert (tmp_path / "docs" / "pipes.md").exists()
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
                return _R(128, out="")  # 两次 push 都失败
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


# ============ 内容大类(JSON/分类扩展后) ============


class TestContentCategories:
    def test_paper_sources_not_buried(self, db_session):
        _pipe(db_session, "rss", "HuggingFace 每日论文",
              {"feed_url": "http://127.0.0.1:9002/huggingface/daily-papers"})
        _pipe(db_session, "rss", "BAIR Berkeley",
              {"feed_url": "https://bair.berkeley.edu/blog/feed.xml"})
        db_session.commit()
        md = render_pipes_md(db_session, now=NOW)
        assert "## 论文与研究" in md
        # 不再落回科技媒体
        papers = md.split("## 论文与研究")[1].split("##")[0]
        assert "HuggingFace 每日论文" in papers and "BAIR" in papers

    def test_category_order_and_defaults(self, db_session):
        _pipe(db_session, "rss", "OpenAI News", {"feed_url": "https://openai.com/news/rss.xml"})
        _pipe(db_session, "rss", "HuggingFace Blog", {"feed_url": "https://huggingface.co/blog/feed.xml"})
        _pipe(db_session, "rss", "Hacker News 最佳", {"feed_url": "http://127.0.0.1:9002/hackernews/best"})
        _pipe(db_session, "rss", "Simon Willison", {"feed_url": "https://simonwillison.net/atom/everything/"})
        _pipe(db_session, "rss", "36氪", {"feed_url": "http://127.0.0.1:9002/36kr/news/latest"})
        _pipe(db_session, "wechat", "机器之心", {"mp_id": "x"})
        db_session.commit()
        md = render_pipes_md(db_session, now=NOW)
        assert "## 厂商官方" in md and "OpenAI News" in md.split("## 厂商官方")[1].split("##")[0]
        assert "HuggingFace Blog" in md.split("## 厂商官方")[1].split("##")[0]
        assert "## 开发者与独立博客" in md
        dev = md.split("## 开发者与独立博客")[1].split("##")[0]
        assert "Hacker News" in dev and "Simon Willison" in dev
        media = md.split("## 科技媒体")[1].split("##")[0]
        assert "36氪" in media
        assert "机器之心" in md.split("## 微信公众号")[1].split("##")[0]

    def test_config_category_overrides_rules(self, db_session):
        _pipe(db_session, "rss", "自定义源", {"feed_url": "https://x.com/f", "export_category": "论文与研究"})
        db_session.commit()
        md = render_pipes_md(db_session, now=NOW)
        assert "自定义源" in md.split("## 论文与研究")[1].split("##")[0]

    def test_research_blogs_in_paper_group(self, db_session):
        for name in ("Google Research", "DeepMind", "Microsoft Research"):
            _pipe(db_session, "rss", name, {"feed_url": f"https://{name}.com/rss"})
        db_session.commit()
        md = render_pipes_md(db_session, now=NOW)
        papers = md.split("## 论文与研究")[1].split("##")[0]
        assert all(n in papers for n in ("Google Research", "DeepMind", "Microsoft Research"))


# ============ pipes.json 构建 ============


class TestBuildPipesJson:
    def test_structure_and_roundtrip_fields(self, db_session, six_types):
        from app.services.pipe_export import build_pipes_json

        data = json.loads(build_pipes_json(db_session, now=NOW))
        assert data["version"] == 1 and data["exported_at"] == NOW
        assert len(data["pipes"]) == 7
        gh_derived = next(p for p in data["pipes"] if p["name"] == "gh-LLM")
        assert gh_derived["domain"] == "LLM"          # 名字而非 id
        assert gh_derived["type"] == "github"
        wx = next(p for p in data["pipes"] if p["type"] == "wechat")
        assert wx["config"]["mp_id"] == "xyz"          # 完整 config
        assert set(data["pipes"][0]) == {"type", "name", "config", "domain",
                                         "enabled", "fetch_interval_min"}

    def test_empty_db(self, db_session):
        from app.services.pipe_export import build_pipes_json

        data = json.loads(build_pipes_json(db_session, now=NOW))
        assert data["pipes"] == []
