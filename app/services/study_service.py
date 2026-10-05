"""学习工作台载荷:近 N 天文档的学习视图数据一次组装。

纯读组装,不写库;判定取每文档最新一条 ok analysis(append-only 下去重),
领域/管道给名字,reading 给已读/收藏初值。页面与 API 都是薄壳。
"""

import json
import time

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (Analysis, Article, Doc, Discovery, Domain, Membership,
                        Paper, Reading, Pipe, Repo)


def collect_study_docs(db: Session, days: int) -> list[dict]:
    """近 days 天文档的学习载荷,星级降序、时间降序。

    未判定文档保留(stars=0);多条 ok 判定只取最新一条,不产生重复文档。
    """
    since = int(time.time()) - days * 86400
    docs = (
        db.query(Doc)
        .filter(Doc.sort_time >= since)
        .order_by(Doc.sort_time.desc())
        .all()
    )
    if not docs:
        return []
    ids = [d.id for d in docs]

    stars = _latest_ok_analyses(db, ids)
    domains = _domain_names(db, ids)
    pipes = _pipe_names(db, ids)
    readings = _reading_flags(db, ids)
    extras = _entity_extras(db, ids)

    out = []
    for doc in docs:
        analysis = stars.get(doc.id)
        author, words = extras.get(doc.id, ("", 0))
        out.append({
            "id": doc.id,
            "kind": doc.kind,
            "url": doc.url,
            "title": doc.title,
            "ts": doc.sort_time,
            "stars": analysis.stars if analysis else 0,
            "summary": analysis.summary if analysis else "",
            "keypoints": (json.loads(analysis.keypoints) if analysis and analysis.keypoints else []),
            "article_kind": analysis.article_kind if analysis else "",
            "domains": domains.get(doc.id, []),
            "pipes": pipes.get(doc.id, []),
            "is_read": bool(readings.get(doc.id, {}).get("is_read")),
            "is_favorite": bool(readings.get(doc.id, {}).get("is_favorite")),
            "author": author,
            "words": words,
        })
    out.sort(key=lambda x: (-x["stars"], -(x["ts"] or 0)))
    return out


def _latest_ok_analyses(db: Session, doc_ids: list[int]) -> dict[int, Analysis]:
    """每文档最新一条 ok analysis;无 ok 的文档不在结果里。"""
    latest_ids = (
        db.query(func.max(Analysis.id))
        .filter(Analysis.doc_id.in_(doc_ids), Analysis.status == "ok")
        .group_by(Analysis.doc_id)
        .all()
    )
    id_list = [row[0] for row in latest_ids]
    if not id_list:
        return {}
    rows = db.query(Analysis).filter(Analysis.id.in_(id_list)).all()
    return {a.doc_id: a for a in rows}


def _domain_names(db: Session, doc_ids: list[int]) -> dict[int, list[str]]:
    rows = (
        db.query(Membership.doc_id, Domain.name)
        .join(Domain, Domain.id == Membership.domain_id)
        .filter(Membership.doc_id.in_(doc_ids))
        .all()
    )
    out: dict[int, list[str]] = {}
    for doc_id, name in rows:
        out.setdefault(doc_id, []).append(name)
    return out


def _pipe_names(db: Session, doc_ids: list[int]) -> dict[int, list[str]]:
    rows = (
        db.query(Discovery.doc_id, Pipe.name)
        .join(Pipe, Pipe.id == Discovery.pipe_id)
        .filter(Discovery.doc_id.in_(doc_ids))
        .distinct()
        .all()
    )
    out: dict[int, list[str]] = {}
    for doc_id, name in rows:
        out.setdefault(doc_id, []).append(name)
    return out


def _reading_flags(db: Session, doc_ids: list[int]) -> dict[int, dict]:
    rows = db.query(Reading).filter(Reading.doc_id.in_(doc_ids)).all()
    return {
        r.doc_id: {"is_read": r.is_read, "is_favorite": r.is_favorite}
        for r in rows
    }


def _entity_extras(db: Session, doc_ids: list[int]) -> dict[int, tuple[str, int]]:
    """(作者, 字数) 近似值:文章用一等字段,论文/仓库用正文长度近似。"""
    out: dict[int, tuple[str, int]] = {}
    for row in (
        db.query(Article.id, Article.author, Article.word_count)
        .filter(Article.id.in_(doc_ids))
        .all()
    ):
        out[row[0]] = (row[1] or "", row[2] or 0)
    for row in (
        db.query(Paper.id, Paper.authors, Paper.content_text, Paper.abstract)
        .filter(Paper.id.in_(doc_ids))
        .all()
    ):
        text = row[2] or row[3] or ""
        out[row[0]] = ((row[1] or "")[:80], len(text))
    for row in (
        db.query(Repo.id, Repo.description, Repo.readme_text)
        .filter(Repo.id.in_(doc_ids))
        .all()
    ):
        out[row[0]] = ("", len(row[2] or ""))
    return out
