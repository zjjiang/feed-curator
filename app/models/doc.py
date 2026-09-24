from typing import Literal

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, Text

from app.models.base import Base, LongText

Kind = Literal["paper", "repo", "article"]


class Doc(Base):
    """文档身份层:三类实体共享的最简信息,统一标识空间与排序键。"""

    __tablename__ = "doc"
    __table_args__ = (
        CheckConstraint("kind IN ('paper', 'repo', 'article')", name="ck_doc_kind"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(16), nullable=False)
    # 归一化 URL,全局去重键。500 字符 × utf8mb4 4字节 = 2000 字节,低于 InnoDB 索引上限
    url_key = Column(String(500), nullable=False, unique=True)
    url = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    sort_time = Column(Integer, index=True)  # 跨实体统一排序;可空(无发布时间的文档)
    first_seen_at = Column(Integer, nullable=False)
    last_modified_at = Column(Integer, nullable=False)


class Paper(Base):
    """论文实体。id 同时为 PK 与 doc.id 的 FK。"""

    __tablename__ = "paper"

    id = Column(Integer, ForeignKey("doc.id"), primary_key=True)
    abstract = Column(LongText)
    content_text = Column(LongText)
    authors = Column(Text)      # JSON 数组字符串,ensure_ascii=False
    categories = Column(Text)   # JSON 数组字符串(arXiv 分类如 cs.AI)
    pdf_url = Column(Text)
    arxiv_id = Column(String(64), index=True)   # 2606.02578;非 arXiv 论文为空
    version = Column(String(8))                 # v1
    submitted_at = Column(Integer)
    extra = Column(LongText)    # JSON 对象字符串:源侧策展信号(upvotes/githubRepo 等)


class Repo(Base):
    """工程项目实体。"""

    __tablename__ = "repo"

    id = Column(Integer, ForeignKey("doc.id"), primary_key=True)
    owner = Column(String(255))
    name = Column(String(255))
    description = Column(Text)
    readme_text = Column(LongText)
    stars = Column(Integer, index=True)
    stars_prev = Column(Integer)     # 上次刷新时的星标;增量计算的基准
    stars_gained = Column(Integer)   # 相对上次刷新的星标增量,可负;首次刷新为 NULL
    forks = Column(Integer)
    open_issues = Column(Integer)
    language = Column(String(64), index=True)
    topics = Column(Text)       # JSON 数组字符串
    license = Column(String(64))    # SPDX id,如 MIT
    pushed_at = Column(Integer)
    refreshed_at = Column(Integer)


class Article(Base):
    """文章实体。kind_tag 为 AI 判定的物化结果(tech|business),正文不足时为空。"""

    __tablename__ = "article"

    id = Column(Integer, ForeignKey("doc.id"), primary_key=True)
    author = Column(Text)       # 不用 String(255):作者列表可能很长(沿用旧表结论)
    description = Column(LongText)
    content_text = Column(LongText)
    content_html = Column(LongText)
    cover_image_url = Column(Text)
    word_count = Column(Integer, default=0)
    kind_tag = Column(String(16))   # tech|business|NULL
    published_at = Column(Integer)
    source_meta = Column(Text)  # JSON 字符串:来源侧杂项字段
