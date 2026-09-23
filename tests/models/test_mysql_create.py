import os

import pytest
from sqlalchemy import create_engine, inspect

from app.models import Base

MYSQL_URL = os.environ.get("DATABASE_URL", "").strip()


@pytest.mark.skipif(not MYSQL_URL.startswith("mysql"), reason="DATABASE_URL 未指向 MySQL")
def test_create_all_on_real_mysql():
    """真连 MySQL 验证 create_all 可执行(迁移目标库,create_all 幂等)。"""
    engine = create_engine(MYSQL_URL, future=True)
    try:
        Base.metadata.create_all(engine)
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        assert {"doc", "paper", "repo", "article", "domain", "pipe", "discovery",
                "analysis", "membership", "document_link", "reading", "suggestion",
                "run_log"} <= tables
    finally:
        engine.dispose()
