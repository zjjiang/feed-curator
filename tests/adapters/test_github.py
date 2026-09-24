"""GitHubAdapter 与检索串组装的单元测试(客户端用假类替换,不发网络)。"""

import re
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.github import (
    MAX_QUERY_LEN,
    GitHubAdapter,
    ascii_keywords,
    build_query,
)


class TestDefaults:
    def test_derived_config_without_params_uses_defaults(self):
        q = GitHubAdapter._query_from_config({"keywords": ["VLA"]})
        assert "stars:>=30" in q
        assert "created:>=" in q
        assert "VLA" in q


class TestAsciiKeywords:
    def test_drops_non_ascii_terms(self):
        kws = ["具身智能", "Embodied AI", "人形机器人", "VLA"]
        assert ascii_keywords(kws) == ["Embodied AI", "VLA"]

    def test_mixed_language_term_dropped_whole(self):
        assert ascii_keywords(["VLA具身"]) == []

    def test_empty_input(self):
        assert ascii_keywords([]) == []
        assert ascii_keywords(None) == []


class TestBuildQuery:
    def test_or_join_and_phrase_quoting(self):
        q = build_query(["VLA", "Robot Learning", "Manipulation"],
                        window_days=7, min_stars=50,
                        now=datetime(2026, 9, 24, tzinfo=UTC))
        assert q == ('VLA OR "Robot Learning" OR Manipulation '
                     "created:>=2026-09-17 stars:>=50")

    def test_window_rolls_with_now(self):
        early = build_query(["VLA"], window_days=7, min_stars=50,
                            now=datetime(2026, 1, 1, tzinfo=UTC))
        late = build_query(["VLA"], window_days=7, min_stars=50,
                           now=datetime(2026, 9, 24, tzinfo=UTC))
        assert "created:>=2025-12-25" in early
        assert "created:>=2026-09-17" in late

    def test_all_non_ascii_yields_none(self):
        assert build_query(["具身智能", "人形机器人"], window_days=7,
                           min_stars=50) is None

    def test_truncation_keeps_qualifiers_and_balanced_quotes(self):
        keywords = [f"kw{i} " + "x" * 55 for i in range(6)]
        q = build_query(keywords, window_days=7, min_stars=50,
                        now=datetime(2026, 9, 24, tzinfo=UTC))
        assert len(q) <= MAX_QUERY_LEN
        assert q.count('"') % 2 == 0
        assert q.endswith("stars:>=50")
        assert "created:>=" in q

    def test_more_than_five_operators_capped(self):
        # GitHub 限制最多 5 个 OR 算子 → 只保留前 6 个词条
        keywords = [f"t{i}" for i in range(9)]
        q = build_query(keywords, window_days=7, min_stars=50)
        assert q.count(" OR ") == 5
        for i in range(6):
            assert f"t{i}" in q
        assert "t6" not in q and "t8" not in q

    def test_truncation_does_not_leave_trailing_or(self):
        keywords = ["a" * 40 for _ in range(6)]
        q = build_query(keywords, window_days=7, min_stars=50)
        assert not q.rstrip().endswith("OR")

    def test_overly_long_keyword_term_itself_truncated(self):
        q = build_query(["a" * 400], window_days=7, min_stars=50)
        assert q is not None and len(q) <= MAX_QUERY_LEN


class TestGitHubAdapter:
    def _items(self, *full_names_stars):
        return [
            {
                "full_name": n, "html_url": f"https://github.com/{n}",
                "description": f"desc of {n}",
                "stargazers_count": s, "forks_count": 5,
                "open_issues_count": 1, "language": "Python",
                "topics": ["vla"], "license": {"spdx_id": "MIT"},
                "pushed_at": "2026-09-20T08:00:00Z",
            }
            for n, s in full_names_stars
        ]

    class _FakeClient:
        captured_query = None
        captured_per_page = None

        def __init__(self, items):
            self._items = items

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def search_repositories(self, query, *, per_page=30):
            type(self).captured_query = query
            type(self).captured_per_page = per_page
            return self._items

    def test_derived_keywords_build_query_and_map_items(self, monkeypatch):
        fake = type(self)._FakeClient(self._items(("acme/vla", 100)))
        monkeypatch.setattr("app.adapters.github.GitHubClient", lambda: fake)

        items = GitHubAdapter().fetch({
            "keywords": ["VLA", "具身智能"],
            "window_days": 7, "min_stars": 50,
        })

        q = type(self)._FakeClient.captured_query
        assert "VLA" in q and "具身智能" not in q
        assert re.search(r"created:>=\d{4}-\d{2}-\d{2}", q)
        assert "stars:>=50" in q

        assert len(items) == 1
        fi = items[0]
        assert fi.external_id == "acme/vla"
        assert fi.title == "acme/vla"
        assert fi.url == "https://github.com/acme/vla"
        assert fi.description == "desc of acme/vla"
        expected = int(datetime(2026, 9, 20, 8, 0, 0, tzinfo=UTC).timestamp())
        assert fi.published_at == expected
        assert fi.meta["stars"] == 100
        assert fi.meta["forks"] == 5
        assert fi.meta["open_issues"] == 1
        assert fi.meta["language"] == "Python"
        assert fi.meta["topics"] == ["vla"]
        assert fi.meta["license"] == "MIT"
        assert fi.content_text is None

    def test_shared_pipe_uses_config_query_verbatim(self, monkeypatch):
        fake = type(self)._FakeClient(self._items(("acme/x", 100)))
        monkeypatch.setattr("app.adapters.github.GitHubClient", lambda: fake)

        GitHubAdapter().fetch({"query": "topic:llm stars:>1000"})
        assert type(self)._FakeClient.captured_query == "topic:llm stars:>1000"

    def test_no_query_source_raises_without_hitting_api(self, monkeypatch):
        def boom():
            raise AssertionError("不应发起请求")

        monkeypatch.setattr("app.adapters.github.GitHubClient", boom)
        with pytest.raises(ValueError, match="检索条件"):
            GitHubAdapter().fetch({})

    def test_below_threshold_filtered_client_side(self, monkeypatch):
        fake = type(self)._FakeClient(
            self._items(("acme/hot", 100), ("acme/cold", 10)))
        monkeypatch.setattr("app.adapters.github.GitHubClient", lambda: fake)

        items = GitHubAdapter().fetch({"query": "x", "min_stars": 50})
        assert [fi.external_id for fi in items] == ["acme/hot"]

    def test_default_per_page(self, monkeypatch):
        fake = type(self)._FakeClient([])
        monkeypatch.setattr("app.adapters.github.GitHubClient", lambda: fake)
        GitHubAdapter().fetch({"query": "x"})
        assert type(self)._FakeClient.captured_per_page == 30

    def test_bad_pushed_at_yields_none_sort_time(self, monkeypatch):
        rows = self._items(("acme/x", 100))
        rows[0]["pushed_at"] = "not-a-date"
        fake = type(self)._FakeClient(rows)
        monkeypatch.setattr("app.adapters.github.GitHubClient", lambda: fake)
        items = GitHubAdapter().fetch({"query": "x"})
        assert items[0].published_at is None
