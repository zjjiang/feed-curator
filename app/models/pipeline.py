from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from app.models.base import Base


class Domain(Base):
    """领域:用户的订阅单位。keywords 是 JSON 数组字符串(ensure_ascii=False)。"""

    __tablename__ = "domain"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text)
    keywords = Column(Text)     # JSON 数组字符串
    enabled = Column(Integer, default=1, nullable=False)
    created_at = Column(Integer, nullable=False)
    updated_at = Column(Integer, nullable=False)


class Pipe(Base):
    """管道:文档进入系统的通道。domain_id 为空 = 共享管道,有值 = 该领域的派生管道。"""

    __tablename__ = "pipe"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(32), nullable=False)   # rss|arxiv|wechat|github|hf_papers|manual
    name = Column(String(255), nullable=False)
    config = Column(Text, nullable=False)       # JSON 字符串
    domain_id = Column(Integer, ForeignKey("domain.id"))
    enabled = Column(Integer, default=1, nullable=False)
    fetch_interval_min = Column(Integer, default=30, nullable=False)
    last_fetched_at = Column(Integer)
    last_error = Column(Text)
    created_at = Column(Integer, nullable=False)
    updated_at = Column(Integer, nullable=False)


class Discovery(Base):
    """采集溯源:哪个管道在何时采到了哪个文档。同一文档被多个管道采集时各留一条。"""

    __tablename__ = "discovery"
    __table_args__ = (
        UniqueConstraint("pipe_id", "external_id", name="uq_discovery_pipe_external"),
        Index("idx_discovery_doc", "doc_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    pipe_id = Column(Integer, ForeignKey("pipe.id"), nullable=False)
    external_id = Column(String(255), nullable=False)
    doc_id = Column(Integer, ForeignKey("doc.id"), nullable=False)
    first_seen_at = Column(Integer, nullable=False)
