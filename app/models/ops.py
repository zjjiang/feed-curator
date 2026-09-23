from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from app.models.base import Base


class Reading(Base):
    """用户阅读状态,与文档内容分离。行不存在 = 从未操作过(懒创建)。"""

    __tablename__ = "reading"

    doc_id = Column(Integer, ForeignKey("doc.id"), primary_key=True)
    is_read = Column(Integer, default=0, nullable=False)
    is_favorite = Column(Integer, default=0, nullable=False)
    rating = Column(Integer)        # 1-5,可空
    note = Column(Text)
    is_dismissed = Column(Integer, default=0, nullable=False)
    updated_at = Column(Integer, nullable=False)


class DocumentLink(Base):
    """文档间关系(implements/duplicate/cites...)。为后续 agent 预留的核心表。"""

    __tablename__ = "document_link"
    __table_args__ = (
        UniqueConstraint("from_doc_id", "to_doc_id", "kind", name="uq_document_link"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_doc_id = Column(Integer, ForeignKey("doc.id"), nullable=False)
    to_doc_id = Column(Integer, ForeignKey("doc.id"), nullable=False)
    kind = Column(String(32), nullable=False)
    note = Column(Text)
    created_at = Column(Integer, nullable=False)


class Suggestion(Base):
    """agent 建议:agent MUST NOT 直接改数据,产出建议由用户确认后生效。"""

    __tablename__ = "suggestion"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(64), nullable=False)       # propose_keyword|link_documents|...
    payload = Column(Text, nullable=False)          # JSON 字符串
    status = Column(String(16), nullable=False, default="pending")  # pending|accepted|rejected
    created_by = Column(String(64), nullable=False) # 产出建议的 agent 标识
    created_at = Column(Integer, nullable=False)
    decided_at = Column(Integer)


class RunLog(Base):
    """运行记录:Job 与 SyncLog 的合并。kind=fetch 时 total/processed 用不上,接受。"""

    __tablename__ = "run_log"
    __table_args__ = (
        Index("idx_runlog_pipe_created", "pipe_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(16), nullable=False)       # fetch|analyze|agent
    pipe_id = Column(Integer, ForeignKey("pipe.id"))    # fetch 类记录指向管道
    pipe_name = Column(String(255))                 # 冗余存名字,管道删除后日志仍可读
    trigger = Column(String(16), nullable=False, default="manual")  # manual|auto
    status = Column(String(16), nullable=False, default="running")  # running|done|failed|cancelled
    total = Column(Integer, default=0, nullable=False)
    processed = Column(Integer, default=0, nullable=False)
    succeeded = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    inserted = Column(Integer, default=0, nullable=False)   # fetch 类:本次新入库文档数
    error = Column(Text)
    duration_ms = Column(Integer)                   # fetch 类:采集耗时
    created_at = Column(Integer, nullable=False, index=True)
    finished_at = Column(Integer)
