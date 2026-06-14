import os
import time
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.models import Base

# 数据库连接:优先用环境变量 DATABASE_URL(MySQL 等),没有则回退本地 SQLite。
# MySQL 示例:mysql+pymysql://USER:PASSWORD@127.0.0.1:3306/feed_curator?charset=utf8mb4
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,      # 连接前 ping,避免 MySQL 长连接被服务端断开后报错
        pool_recycle=3600,
        future=True,
    )
else:
    DB_PATH = Path(__file__).resolve().parent.parent / "data" / "feed-curator.db"
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
        future=True,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

IS_SQLITE = engine.dialect.name == "sqlite"


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """轻量迁移:create_all 不会改已有表,这里手动补列 / 处理语义变更。

    - 给从旧 schema 升级的 SQLite 库补 ai_keypoints 列(MySQL 是新建库,不需要)。
    - 进程重启时把残留的 running 任务标记为 failed,否则单任务锁的 DB 状态永远卡住。
    """
    insp = inspect(engine)
    tables = insp.get_table_names()
    if "items" not in tables:
        return

    with engine.begin() as conn:
        # 仅 SQLite 旧库需要补列;MySQL 由 create_all 按最新 schema 建表,天然就有该列。
        if IS_SQLITE:
            cols = {c["name"] for c in insp.get_columns("items")}
            if "ai_keypoints" not in cols:
                conn.execute(text("ALTER TABLE items ADD COLUMN ai_keypoints TEXT"))
                conn.execute(text(
                    "UPDATE items SET ai_score=NULL, ai_summary=NULL, "
                    "ai_tags=NULL, ai_scored_at=NULL"
                ))

        # 清理重启遗留的僵尸任务(方言无关:用参数绑定传当前时间戳)
        if "jobs" in tables:
            conn.execute(
                text("UPDATE jobs SET status='failed', error='进程重启,任务中断', "
                     "finished_at=:now WHERE status='running'"),
                {"now": int(time.time())},
            )


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
