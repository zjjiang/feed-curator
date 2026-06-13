from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


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
    external_id = Column(String(512), nullable=False)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    author = Column(String(255))
    description = Column(Text)
    content_text = Column(Text)
    content_html = Column(Text)
    cover_image_url = Column(Text)
    word_count = Column(Integer, default=0)
    published_at = Column(Integer, index=True)
    fetched_at = Column(Integer, nullable=False)
    meta = Column(Text)
    ai_score = Column(Integer)
    ai_summary = Column(Text)
    ai_tags = Column(Text)
    ai_scored_at = Column(Integer)
    is_read = Column(Integer, default=0)
    is_favorite = Column(Integer, default=0)
    user_rating = Column(Integer)

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_items_source_external"),
        Index("idx_items_source_published", "source_id", "published_at"),
    )


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text)
    updated_at = Column(Integer)
