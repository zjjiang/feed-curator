"""hf_papers 适配器:HF Daily Papers 镜像接入、条目映射、回退链与窗口。

fixture 脱敏自 2026-09-23 实测响应的真实形状:JSON 数组,条目为
{"paper": {id/title/summary/authors[]/upvotes/githubRepo/githubStars/publishedAt}}。
"""

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.adapters.hf_papers import HFPapersAdapter
from app.jobs.fetcher import fetch_source
from app.models import Pipe, RunLog

MIRROR = "https://hf-mirror.com"
OFFICIAL = "https://huggingface.co"

NOW_UTC = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)
WINDOW = ["2026-09-24", "2026-09-23", "2026-09-22"]

GOOD_ENTRY = {
    "paper": {
        "id": "2609.25804",
        "title": "VLA Models: A Survey",
        "summary": "This paper surveys vision-language-action models.",
        "authors": [{"name": "张三"}, {"name": "Li Si"}, {"name": "Wang Wu"},
                    {"name": "第四作者"}],
        "upvotes": 42,
        "githubRepo": "acme/vla-lab",
        "githubStars": 321,
        "publishedAt": "2026-09-22T17:59:59.000Z",
    }
}
BARE_ENTRY = {
    "paper": {
        "id": "2609.21001",
        "title": "Manipulation in the wild",
        "summary": "Field study of manipulation.",
        "authors": [{"name": "Solo Author"}],
        "upvotes": 7,
        "publishedAt": "2026-09-22T18:00:00.000Z",
    }
}


def _payload(entries) -> str:
    return json.dumps(entries)


class _FakeOutbound:
    """按 host+date 路由的假出网:script[url] → str(成功)或 Exception。"""

    def __init__(self, monkeypatch, script):
        self.script = script
        self.calls: list[str] = []
        monkeypatch.setattr("app.adapters.hf_papers.outbound.request", self)

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        outcome = self.script[url]
        if isinstance(outcome, Exception):
            raise outcome
        return type("R", (), {"text": outcome})()


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    monkeypatch.setattr("app.adapters.hf_papers._now_utc", lambda: NOW_UTC)


def _script_all_dates(body_factory):
    return {f"{MIRROR}/api/daily_papers?date={d}": body_factory(d) for d in WINDOW}


class TestEntryMapping:
    def test_standard_entry_maps_per_contract(self, monkeypatch):
        _FakeOutbound(monkeypatch, _script_all_dates(lambda d: _payload([GOOD_ENTRY])))
        items = HFPapersAdapter().fetch({})
        assert len(items) == 3  # 三日期窗口,同一篇跨日重复出现
        item = items[0]
        assert item.external_id == "2609.25804"
        assert item.url == "https://arxiv.org/abs/2609.25804"
        assert item.title == "VLA Models: A Survey"
        assert item.description == "This paper surveys vision-language-action models."
        assert item.content_text == item.description
        assert item.meta["all_authors"] == ["张三", "Li Si", "Wang Wu", "第四作者"]
        assert item.author == "张三, Li Si, Wang Wu..."
        assert item.meta["pdf_url"] == "https://arxiv.org/pdf/2609.25804"
        assert item.meta["extra"] == {"upvotes": 42, "githubRepo": "acme/vla-lab",
                                      "githubStars": 321}
        assert item.published_at == int(datetime(
            2026, 9, 22, 17, 59, 59, tzinfo=UTC).timestamp())

    def test_missing_github_fields_yield_partial_extra(self, monkeypatch):
        _FakeOutbound(monkeypatch, _script_all_dates(lambda d: _payload([BARE_ENTRY])))
        item = HFPapersAdapter().fetch({})[0]
        assert item.meta["extra"] == {"upvotes": 7}

    def test_missing_categories_is_not_a_failure(self, monkeypatch):
        _FakeOutbound(monkeypatch, _script_all_dates(lambda d: _payload([BARE_ENTRY])))
        item = HFPapersAdapter().fetch({})[0]
        assert "categories" not in item.meta  # 留空,等召回层补全

    def test_invalid_entry_id_marks_item_error(self, monkeypatch):
        bad = {"paper": {"id": "not-an-arxiv-id", "title": "怪条目",
                         "summary": "x", "authors": [], "upvotes": 1}}
        script = _script_all_dates(lambda d: _payload([GOOD_ENTRY, bad]))
        _FakeOutbound(monkeypatch, script)
        items = HFPapersAdapter().fetch({})
        # 两天正常条目 + 三天怪条目(每窗口一条,均被打上空 URL 标记)
        marked = [i for i in items if i.url == ""]
        assert len(marked) == 3
        assert marked[0].title == "怪条目"

    def test_three_date_window_requested(self, monkeypatch):
        fake = _FakeOutbound(monkeypatch, _script_all_dates(lambda d: _payload([])))
        HFPapersAdapter().fetch({})
        assert [f"{MIRROR}/api/daily_papers?date={d}" for d in WINDOW] == fake.calls

    def test_base_url_config_override(self, monkeypatch):
        alt = "https://my-mirror.example.com"
        script = {f"{alt}/api/daily_papers?date={d}": _payload([]) for d in WINDOW}
        fake = _FakeOutbound(monkeypatch, script)
        HFPapersAdapter().fetch({"base_url": alt})
        assert fake.calls == [f"{alt}/api/daily_papers?date={d}" for d in WINDOW]


class TestFallbackChain:
    def test_mirror_structure_error_falls_back_to_official(self, monkeypatch):
        script = _script_all_dates(lambda d: "<html>维护页</html>")
        for d in WINDOW:
            script[f"{OFFICIAL}/api/daily_papers?date={d}"] = _payload([GOOD_ENTRY])
        fake = _FakeOutbound(monkeypatch, script)
        items = HFPapersAdapter().fetch({})
        assert items and items[0].external_id == "2609.25804"
        assert any(u.startswith(OFFICIAL) for u in fake.calls)

    def test_mirror_connection_error_falls_back(self, monkeypatch):
        script = _script_all_dates(
            lambda d: httpx.ConnectError("镜像连不上"))
        for d in WINDOW:
            script[f"{OFFICIAL}/api/daily_papers?date={d}"] = _payload([GOOD_ENTRY])
        _FakeOutbound(monkeypatch, script)
        items = HFPapersAdapter().fetch({})
        assert len(items) == 3

    def test_non_list_payload_counts_as_structure_error(self, monkeypatch):
        script = _script_all_dates(lambda d: _payload({"detail": "not found"}))
        for d in WINDOW:
            script[f"{OFFICIAL}/api/daily_papers?date={d}"] = _payload([GOOD_ENTRY])
        _FakeOutbound(monkeypatch, script)
        assert HFPapersAdapter().fetch({})

    def test_all_exits_fail_raises_to_fetcher_isolation(self, monkeypatch):
        script = _script_all_dates(lambda d: httpx.ConnectError("全灭"))
        for d in WINDOW:
            script[f"{OFFICIAL}/api/daily_papers?date={d}"] = \
                httpx.ConnectError("官方也不通")
        _FakeOutbound(monkeypatch, script)
        with pytest.raises(RuntimeError, match="全部出口失败"):
            HFPapersAdapter().fetch({})


class TestFetcherIsolation:
    def test_pipe_failure_isolated_and_logged(self, db_session, monkeypatch):
        script = _script_all_dates(lambda d: httpx.ConnectError("全灭"))
        for d in WINDOW:
            script[f"{OFFICIAL}/api/daily_papers?date={d}"] = \
                httpx.ConnectError("官方也不通")
        _FakeOutbound(monkeypatch, script)
        monkeypatch.setattr("app.jobs.fetcher.get_adapter",
                            lambda t: HFPapersAdapter())
        p = Pipe(type="hf_papers", name="HF 精选", config="{}",
                 created_at=1, updated_at=1)
        db_session.add(p)
        db_session.commit()

        inserted, err = fetch_source(db_session, p, trigger="manual")
        assert inserted == 0 and err is not None
        assert p.last_error == err
        log = db_session.query(RunLog).one()
        assert log.status == "failed" and log.kind == "fetch"

    def test_invalid_entry_recorded_as_item_error_in_run_log(
            self, db_session, monkeypatch):
        bad = {"paper": {"id": "怪id", "title": "怪条目", "summary": "x",
                         "authors": [], "upvotes": 1}}
        script = _script_all_dates(lambda d: _payload([GOOD_ENTRY, bad]))
        _FakeOutbound(monkeypatch, script)
        monkeypatch.setattr("app.jobs.fetcher.get_adapter",
                            lambda t: HFPapersAdapter())
        p = Pipe(type="hf_papers", name="HF 精选", config="{}",
                 created_at=1, updated_at=1)
        db_session.add(p)
        db_session.commit()

        inserted, err = fetch_source(db_session, p, trigger="manual")
        # 同一论文跨日重复只记一条 discovery;三条怪条目各记一条条目级错误
        assert inserted == 1
        assert err is not None and "3 条写入失败" in err
        doc_count = db_session.execute(
            __import__("sqlalchemy").text("SELECT COUNT(*) FROM doc")).scalar()
        assert doc_count == 1
