"""create_pipe 的 github 配置校验与表单/API 边界。"""

import json
import time

import pytest

from app.models import Pipe
from app.services.source_service import create_pipe

NOW = int(time.time())


def test_shared_github_requires_query(db_session):
    with pytest.raises(ValueError, match="query"):
        create_pipe(db_session, "github", "GH", {}, 30)


def test_derived_github_allows_empty_config(db_session):
    pipe = create_pipe(db_session, "github", "派生", {}, 30, domain_id=1)
    assert pipe.id is not None
    assert json.loads(pipe.config) == {}


def test_invalid_numeric_fields_rejected(db_session):
    with pytest.raises(ValueError, match="整数"):
        create_pipe(db_session, "github", "GH", {"query": "x",
                                                 "window_days": "abc"}, 30)
    with pytest.raises(ValueError, match="window_days"):
        create_pipe(db_session, "github", "GH",
                    {"query": "x", "window_days": 0}, 30)
    with pytest.raises(ValueError, match="per_page"):
        create_pipe(db_session, "github", "GH",
                    {"query": "x", "per_page": 200}, 30)
    with pytest.raises(ValueError, match="不能为负"):
        create_pipe(db_session, "github", "GH",
                    {"query": "x", "min_stars": -5}, 30)


def test_valid_github_shared_created(db_session):
    pipe = create_pipe(db_session, "github", "GH",
                       {"query": "topic:llm", "min_stars": 100}, 60)
    assert json.loads(pipe.config) == {"query": "topic:llm", "min_stars": 100}


def test_non_github_types_unaffected(db_session):
    pipe = create_pipe(db_session, "rss", "R", {"feed_url": "https://x/feed"}, 30)
    assert pipe.id is not None
