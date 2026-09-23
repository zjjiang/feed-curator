from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text

from app.models.base import Base


class Analysis(Base):
    """AI 判定记录,追加写不覆盖。生效判定 = 该 doc 下最新一条 status='ok'。

    domains / article_kind 是判定原始输出;membership 与 article.kind_tag 是其物化结果。
    """

    __tablename__ = "analysis"
    __table_args__ = (
        Index("idx_analysis_doc_created", "doc_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(Integer, ForeignKey("doc.id"), nullable=False)
    status = Column(String(16), nullable=False)     # ok|failed
    model = Column(String(64))                      # 如 deepseek-chat
    prompt_version = Column(String(32))
    summary = Column(Text)
    keypoints = Column(Text)        # JSON 数组字符串
    domains = Column(Text)          # JSON 数组字符串:判定输出的领域名列表
    article_kind = Column(String(16))   # tech|business|NULL(仅文章实体)
    stars = Column(Integer)         # 1-5;失败记录为 NULL
    error = Column(Text)            # 失败原因
    created_at = Column(Integer, nullable=False)


class Membership(Base):
    """文档与领域的归属关系。assigned_by 区分 ai 与 manual;重判只删改 ai 记录。"""

    __tablename__ = "membership"

    doc_id = Column(Integer, ForeignKey("doc.id"), primary_key=True)
    domain_id = Column(Integer, ForeignKey("domain.id"), primary_key=True)
    assigned_by = Column(String(16), nullable=False)    # ai|manual
    created_at = Column(Integer, nullable=False)
