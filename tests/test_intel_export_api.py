"""intel 导出 API：/api/docs 仓库星标、/api/admin/stats 统计窗口、评级基线措辞。

OpenSpec change: expose-intel-stats-and-repo-stars
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.ai.client import SYSTEM_PROMPT
from app.main import app
from app.db import get_session

NOW = int(time.time())
OLD = NOW - 10 * 86400


@pytest.fixture()
def client(db_session, monkeypatch):
    """同 tests/test_web.py 的 client：内存库 + TestClient。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed(db, *, kind: str, stars: int | None = None):
    """种一个文档；repo 额外给星标数据。"""
    from app.models import Analysis, Doc, Paper, Repo

    doc = Doc(kind=kind, url_key=f"https://e.com/{kind}", url=f"https://e.com/{kind}",
              title=f"t-{kind}", sort_time=NOW - 3600, first_seen_at=OLD,
              last_modified_at=OLD)
    db.add(doc)
    db.flush()
    if kind == "repo":
        db.add(Repo(id=doc.id, owner="o", name="n", readme_text="r" * 200,
                    stars=1234, stars_gained=56))
    elif kind == "paper":
        db.add(Paper(id=doc.id, abstract="a" * 200))
    if stars is not None:
        db.add(Analysis(doc_id=doc.id, status="ok", summary="s", keypoints='["k"]',
                        domains="[]", stars=stars, model="m",
                        prompt_version="v1", created_at=NOW))
    db.commit()
    return doc


class TestDocsRepoStars:
    def test_repo_doc_includes_gh_stars(self, client, db_session):
        _seed(db_session, kind="repo")
        row = client.get("/api/docs").json()["docs"][0]
        assert row["gh_stars"] == 1234
        assert row["gh_stars_gained"] == 56

    def test_non_repo_doc_stars_are_null(self, client, db_session):
        _seed(db_session, kind="paper", stars=4)
        row = client.get("/api/docs").json()["docs"][0]
        assert row["gh_stars"] is None
        assert row["gh_stars_gained"] is None


class TestAdminStatsApi:
    def test_windowed_stats_shape(self, client, db_session):
        _seed(db_session, kind="article", stars=4)
        body = client.get("/api/admin/stats", params={"days": 30})
        assert body.status_code == 200
        data = body.json()
        assert {"daily", "pipes", "domains"} <= set(data)
        assert isinstance(data["daily"], list) and len(data["daily"]) >= 1
        day = data["daily"][-1]
        assert {"date", "ingested", "papers", "repos", "articles",
                "analyzed", "read", "favorited", "ai_high"} <= set(day)
        # 种子文档 10 天前入库，落在窗口内某天，汇总应 ≥1
        assert sum(r["ingested"] for r in data["daily"]) >= 1

    def test_custom_start_end_window(self, client, db_session):
        import datetime
        end = datetime.date.today()
        start = end - datetime.timedelta(days=6)
        body = client.get("/api/admin/stats",
                          params={"start": start.isoformat(), "end": end.isoformat()})
        assert body.status_code == 200
        assert len(body.json()["daily"]) == 7

    def test_invalid_days_falls_back(self, client, db_session):
        """与 /admin/stats 同行为：非法 days 宽容回退默认窗口，不报错。"""
        body = client.get("/api/admin/stats", params={"days": "not-a-number"})
        assert body.status_code == 200
        assert {"daily", "pipes", "domains"} <= set(body.json())

    def test_legacy_global_stats_untouched(self, client, db_session):
        """既有 /api/stats 保持原形状，不受本 change 影响。"""
        body = client.get("/api/stats").json()
        assert {"docs", "by_kind", "orphan_docs"} <= set(body)
        assert "daily" not in body


class TestPromptArticleBaseline:
    def test_system_prompt_has_article_baseline(self):
        """评级条目含文章 4 星基线，且点明论文/项目不适用。"""
        assert "4 星" in SYSTEM_PROMPT and "基线" in SYSTEM_PROMPT
        assert "论文" in SYSTEM_PROMPT and "项目" in SYSTEM_PROMPT
