"""README 补全的单元测试:成功填充/失败保 NULL/不重复抓/补全不产生采集记录。"""

import time

import pytest

from app.models import Discovery, Doc, Pipe, Repo, RunLog
from app.services.repo_enrich import enrich_new_repos, run_readme_backfill

NOW = int(time.time())


class FakeClient:
    """按 full_name 返回预置结果;failures 中的抛异常模拟网络失败。"""

    def __init__(self, responses=None, failures=()):
        self.responses = responses or {}
        self.failures = set(failures)
        self.calls = []

    def get_readme(self, owner: str, name: str):
        key = f"{owner}/{name}"
        self.calls.append(key)
        if key in self.failures:
            raise RuntimeError("网络炸了")
        return self.responses.get(key)

    def close(self):
        pass


def _repo_doc(db, full_name="acme/vla", readme=None):
    owner, name = full_name.split("/")
    doc = Doc(kind="repo", url_key=f"https://github.com/{full_name}",
              url=f"https://github.com/{full_name}", title=full_name,
              first_seen_at=NOW, last_modified_at=NOW)
    db.add(doc)
    db.flush()
    db.add(Repo(id=doc.id, owner=owner, name=name, readme_text=readme))
    db.commit()
    return doc.id


class TestEnrichNewRepos:
    def test_fills_readme(self, db_session):
        doc_id = _repo_doc(db_session)
        client = FakeClient({"acme/vla": "# VLA Robot\n正文"})

        ok = enrich_new_repos(db_session, [(doc_id, "acme", "vla")], client=client)

        assert ok == 1
        repo = db_session.get(Repo, doc_id)
        assert repo.readme_text == "# VLA Robot\n正文"

    def test_failure_keeps_null_and_does_not_raise(self, db_session):
        doc_id = _repo_doc(db_session)
        client = FakeClient(failures={"acme/vla"})

        ok = enrich_new_repos(db_session, [(doc_id, "acme", "vla")], client=client)

        assert ok == 0
        assert db_session.get(Repo, doc_id).readme_text is None

    def test_missing_readme_marked_checked(self, db_session):
        doc_id = _repo_doc(db_session)
        client = FakeClient({})  # 404 → None

        ok = enrich_new_repos(db_session, [(doc_id, "acme", "vla")], client=client)

        assert ok == 1
        repo = db_session.get(Repo, doc_id)
        assert repo.readme_text == ""  # 已确认无 README,不再重试

    def test_skips_already_filled(self, db_session):
        doc_id = _repo_doc(db_session, readme="已有内容")
        client = FakeClient({"acme/vla": "新的"})

        ok = enrich_new_repos(db_session, [(doc_id, "acme", "vla")], client=client)

        assert ok == 0
        assert client.calls == []  # 已有 README 不发请求
        assert db_session.get(Repo, doc_id).readme_text == "已有内容"

    def test_enrich_writes_no_discovery_or_fetch_log(self, db_session):
        doc_id = _repo_doc(db_session)
        pipe = Pipe(type="rss", name="p", config="{}", created_at=NOW, updated_at=NOW)
        db_session.add(pipe)
        db_session.commit()

        enrich_new_repos(db_session, [(doc_id, "acme", "vla")],
                         client=FakeClient({"acme/vla": "# r"}))

        assert db_session.query(Discovery).count() == 0
        assert db_session.query(RunLog).count() == 0

    def test_empty_entries_no_client_activity(self, db_session):
        client = FakeClient()
        assert enrich_new_repos(db_session, [], client=client) == 0
        assert client.calls == []


class TestRunReadmeBackfill:
    def test_backfills_only_null_readmes_with_limit(self, db_session):
        id_a = _repo_doc(db_session, "acme/a")
        _repo_doc(db_session, "acme/b", readme="已有")
        id_c = _repo_doc(db_session, "acme/c")
        id_d = _repo_doc(db_session, "acme/d")  # 排在 c 后,limit=2 时被挤掉
        client = FakeClient({"acme/a": "A", "acme/c": "C", "acme/d": "D"})

        stats = run_readme_backfill(db_session, limit=2, sleep_seconds=0,
                                    client=client)

        assert stats == {"total": 2, "succeeded": 2, "failed": 0}
        assert db_session.get(Repo, id_a).readme_text == "A"
        assert db_session.get(Repo, id_c).readme_text == "C"
        assert db_session.get(Repo, id_d).readme_text is None

    def test_backfill_writes_run_log(self, db_session):
        _repo_doc(db_session, "acme/a")
        run_readme_backfill(db_session, sleep_seconds=0,
                            client=FakeClient({"acme/a": "A"}))
        run = db_session.query(RunLog).one()
        assert run.kind == "readme"
        assert run.status == "done"
        assert run.total == 1 and run.succeeded == 1
        assert run.finished_at is not None

    def test_backfill_failure_isolated(self, db_session):
        id_a = _repo_doc(db_session, "acme/a")
        id_b = _repo_doc(db_session, "acme/b")
        client = FakeClient({"acme/b": "B"}, failures={"acme/a"})

        stats = run_readme_backfill(db_session, sleep_seconds=0, client=client)

        assert stats["failed"] == 1 and stats["succeeded"] == 1
        assert db_session.get(Repo, id_a).readme_text is None
        assert db_session.get(Repo, id_b).readme_text == "B"
        run = db_session.query(RunLog).one()
        assert run.failed == 1 and run.status == "done"
