"""导入 pipes.json:边界校验、去重、未知领域跳过、非法输入零写入。"""

import json

import pytest

from app.models import Domain, Pipe
from app.services.pipe_import import import_pipes

DAY_AGO = 1759000000


def _pipe(db, type_, name, config="{}", domain_id=None):
    p = Pipe(type=type_, name=name, config=config, domain_id=domain_id,
             enabled=1, fetch_interval_min=30, created_at=DAY_AGO,
             updated_at=DAY_AGO)
    db.add(p)
    db.flush()
    return p


def _payload(*pipes):
    return json.dumps({"version": 1, "exported_at": DAY_AGO, "pipes": list(pipes)},
                      ensure_ascii=False)


class TestImportPipes:
    def test_imports_new_pipes(self, db_session):
        out = import_pipes(db_session, _payload(
            {"type": "rss", "name": "新源", "config": {"feed_url": "https://a.com/f"},
             "domain": None, "enabled": 1, "fetch_interval_min": 60}))
        assert out["imported"] == 1 and out["skipped"] == []
        p = db_session.query(Pipe).filter_by(name="新源").one()
        assert p.type == "rss" and p.fetch_interval_min == 60

    def test_bare_list_accepted(self, db_session):
        out = import_pipes(db_session, json.dumps(
            [{"type": "rss", "name": "裸数组源", "config": {"feed_url": "https://b.com/f"}}]))
        assert out["imported"] == 1

    def test_duplicate_type_name_skipped(self, db_session):
        _pipe(db_session, "rss", "已有", '{"feed_url": "https://old.com/f"}')
        out = import_pipes(db_session, _payload(
            {"type": "rss", "name": "已有", "config": {"feed_url": "https://new.com/f"}}))
        assert out["imported"] == 0
        assert any("重复" in r["reason"] for r in out["skipped"])
        assert db_session.query(Pipe).filter_by(name="已有").one().config \
            == '{"feed_url": "https://old.com/f"}'  # 未被改动

    def test_unknown_domain_skipped(self, db_session):
        out = import_pipes(db_session, _payload(
            {"type": "github", "name": "派生", "config": {},
             "domain": "不存在的领域"}))
        assert out["imported"] == 0
        assert any("领域" in r["reason"] for r in out["skipped"])

    def test_known_domain_mapped(self, db_session):
        d = Domain(name="LLM", keywords='[]', enabled=1,
                   created_at=DAY_AGO, updated_at=DAY_AGO)
        db_session.add(d)
        db_session.commit()
        out = import_pipes(db_session, _payload(
            {"type": "github", "name": "gh-LLM", "config": {}, "domain": "LLM"}))
        assert out["imported"] == 1
        assert db_session.query(Pipe).filter_by(name="gh-LLM").one().domain_id == d.id

    def test_invalid_entry_skipped_not_fatal(self, db_session):
        out = import_pipes(db_session, _payload(
            {"type": "未知类型", "name": "坏类型", "config": {}},
            {"type": "rss", "name": "", "config": {}},
            {"type": "rss", "name": "好源", "config": {"feed_url": "https://c.com/f"}}))
        assert out["imported"] == 1
        assert len(out["skipped"]) == 2

    @pytest.mark.parametrize("bad", ["not json", '{"foo": 1}', "123"])
    def test_invalid_input_rejects_all(self, db_session, bad):
        n_before = db_session.query(Pipe).count()
        with pytest.raises(ValueError):
            import_pipes(db_session, bad)
        assert db_session.query(Pipe).count() == n_before  # 零写入

    def test_garbage_entries_reported_not_fatal(self, db_session):
        """合法数组装垃圾条目:跳过并报告,不抛错。"""
        out = import_pipes(db_session, json.dumps(["str", 42]))
        assert out["imported"] == 0
        assert len(out["skipped"]) == 2
