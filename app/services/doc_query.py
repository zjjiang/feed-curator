"""文档列表共享查询 —— REST 与网页版共用的排序/筛选子查询，避免两份实现漂移。"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Analysis


def latest_ok_analysis_ids(db: Session):
    """每篇文档最新一条 ok 判定的 analysis.id（子查询，供 outerjoin 星级排序/门槛）。"""
    return (
        db.query(Analysis.doc_id, func.max(Analysis.id).label("aid"))
        .filter(Analysis.status == "ok")
        .group_by(Analysis.doc_id)
        .subquery()
    )
