from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# 长文本类型:SQLite 下是 TEXT(无长度限制),MySQL 下 TEXT 仅 64KB 不够装长正文,
# 故用 LONGTEXT(最大 4GB)。with_variant 让两个方言各取所需。
LongText = Text().with_variant(LONGTEXT, "mysql")


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(32), nullable=False)
    name = Column(String(255), nullable=False)
    config = Column(Text, nullable=False)
    enabled = Column(Integer, default=1, nullable=False)
    fetch_interval_min = Column(Integer, default=30, nullable=False)
    last_fetched_at = Column(Integer)
    last_error = Column(Text)
    created_at = Column(Integer, nullable=False)
    updated_at = Column(Integer, nullable=False)


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, nullable=False, index=True)
    source_type = Column(String(32), nullable=False)
    external_id = Column(String(255), nullable=False)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    author = Column(Text)
    description = Column(LongText)
    content_text = Column(LongText)
    content_html = Column(LongText)
    cover_image_url = Column(Text)
    word_count = Column(Integer, default=0)
    published_at = Column(Integer, index=True)
    fetched_at = Column(Integer, nullable=False)
    meta = Column(Text)
    ai_score = Column(Integer)          # 1-5 星;None=未处理(待处理);-1=处理失败
    ai_summary = Column(Text)           # AI 生成的摘要(一段浓缩)
    ai_keypoints = Column(Text)         # AI 提炼的要点,JSON 数组字符串
    ai_tags = Column(Text)              # AI 选中的分类标签,JSON 数组字符串
    ai_scored_at = Column(Integer)
    is_read = Column(Integer, default=0)
    is_favorite = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_items_source_external"),
        Index("idx_items_source_published", "source_id", "published_at"),
    )


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text)
    updated_at = Column(Integer)


class Job(Base):
    """一次 AI 处理任务。人工全量触发或系统自动触发都建一条。"""
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(16), nullable=False, default="running")  # running/done/failed/cancelled
    trigger = Column(String(16), nullable=False, default="manual")   # manual/auto
    total = Column(Integer, default=0, nullable=False)
    processed = Column(Integer, default=0, nullable=False)
    succeeded = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    error = Column(Text)
    created_at = Column(Integer, nullable=False)
    finished_at = Column(Integer)


class SyncLog(Base):
    """一次源抓取的记录。每次 fetch_source 写一条，用于运维看板观察同步历史。"""
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, nullable=False, index=True)
    source_name = Column(String(255), nullable=False)   # 冗余存名字，源被删后日志仍可读
    source_type = Column(String(32), nullable=False)
    trigger = Column(String(16), nullable=False, default="auto")  # auto/manual
    ok = Column(Integer, nullable=False, default=1)      # 1=成功 0=失败
    inserted = Column(Integer, nullable=False, default=0)  # 本次新入库条数
    error = Column(Text)                                  # 失败时的错误信息
    duration_ms = Column(Integer)                         # 抓取耗时（毫秒）
    created_at = Column(Integer, nullable=False, index=True)

    __table_args__ = (
        Index("idx_synclogs_source_created", "source_id", "created_at"),
    )
