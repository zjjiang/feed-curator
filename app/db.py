import os
import time
from pathlib import Path
from sqlalchemy import create_engine, text
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
    _cleanup_zombie_runs()
    _report_orphans()


def _cleanup_zombie_runs() -> None:
    """进程重启时把残留的 running 任务标记为 failed,否则单任务锁的 DB 状态永远卡住。"""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE run_log SET status='failed', error='进程重启,任务中断', "
                 "finished_at=:now WHERE kind='analyze' AND status='running'"),
            {"now": int(time.time())},
        )


def _report_orphans() -> None:
    """启动时报告孤儿 doc 数量(只报告不清理——孤儿意味着写入路径有 bug)。"""
    from app.writer import check_orphans

    db = SessionLocal()
    try:
        n = check_orphans(db)
        if n:
            print(f"[init_db] 警告:发现 {n} 个孤儿 doc(kind 有值但实体表缺行),请排查写入路径")
    finally:
        db.close()


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
