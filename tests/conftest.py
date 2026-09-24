import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base

_PROXY_ENV_VARS = (
    "OUTBOUND_PROXY", "CLASH_PROXY_PORT",
    "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
    "ALL_PROXY", "all_proxy",
)


@pytest.fixture(autouse=True)
def _isolate_proxy_env(monkeypatch):
    """出网收口按环境变量选路;测试一律从无代理环境出发,需要代理语义的
    用例(如 tests/utils/test_outbound.py)自行 setenv。否则开发者本机的
    常驻代理变量会泄入 MockTransport 用例,导致意外走真实代理拨号。"""
    for var in _PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def db_session():
    """每个测试独立的内存 SQLite,与 data/feed-curator.db 完全隔离。

    StaticPool 保证全线程共用一条连接:TestClient 的请求跑在 worker 线程,
    普通内存库会按线程各开一个空库。
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
