"""文档单一写入口。

所有文档写入(upsert_doc)与仓库刷新(refresh_repo)必须经过这里,
保证 doc + 实体表 + discovery 在同一事务内落库,杜绝孤儿 doc。
"""

import time
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Article, Discovery, Doc, Paper, Repo
from app.utils.url_key import normalize_url

_ENTITY_MODEL = {"paper": Paper, "repo": Repo, "article": Article}

# 再次采集允许补空的 paper 字段;实体固有属性照常一等存储(design 决策 3)
_PAPER_BACKFILL_FIELDS = ("abstract", "content_text", "authors", "categories",
                          "pdf_url", "version", "submitted_at", "extra")


@dataclass(frozen=True)
class UpsertResult:
    doc_id: int
    doc_created: bool
    discovery_created: bool


def _now() -> int:
    return int(time.time())


def upsert_doc(
    db: Session,
    *,
    kind: str,
    url: str,
    title: str,
    detail: dict,
    pipe_id: int,
    external_id: str,
    sort_time: int | None = None,
    first_seen_at: int | None = None,
    last_modified_at: int | None = None,
) -> UpsertResult:
    """写入一篇文档:doc + 实体表 + discovery 同事务。

    - url 已按 normalize_url 归一化为 url_key,全局去重;
    - 同 url_key 只存一份内容,但每个 (pipe_id, external_id) 各留一条 discovery;
    - first/last_seen_at 缺省取当前时间;迁移脚本可显式传入历史时间。
    """
    url_key = normalize_url(url)
    now = first_seen_at if first_seen_at is not None else _now()
    modified = last_modified_at if last_modified_at is not None else now

    existing = db.query(Doc).filter(Doc.url_key == url_key).first()
    if existing is not None:
        _backfill_paper(db, existing, kind, detail)
        seen = (
            db.query(Discovery)
            .filter(Discovery.pipe_id == pipe_id, Discovery.external_id == external_id)
            .first()
        )
        if seen is not None:
            return UpsertResult(existing.id, False, False)
        db.add(Discovery(pipe_id=pipe_id, external_id=external_id,
                         doc_id=existing.id, first_seen_at=now))
        _commit(db)
        return UpsertResult(existing.id, False, True)

    doc = Doc(
        kind=kind,
        url_key=url_key,
        url=url_key,
        title=title,
        sort_time=sort_time,
        first_seen_at=now,
        last_modified_at=modified,
    )
    db.add(doc)
    db.flush()
    try:
        db.add(_ENTITY_MODEL[kind](id=doc.id, **detail))
        db.add(Discovery(pipe_id=pipe_id, external_id=external_id,
                         doc_id=doc.id, first_seen_at=now))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return UpsertResult(doc.id, True, True)


def _backfill_paper(db: Session, doc: Doc, kind: str, detail: dict) -> None:
    """已存在 doc 再次采集时补齐 paper 实体行的空字段;非空不覆盖。

    补空是自愈式字段补全(策展层先到的论文由召回层补全),只在确实
    补进数据时提交;无可补字段时实体行一个字节都不动。不触碰判定结果。
    """
    if kind != "paper":
        return
    paper = db.get(Paper, doc.id)
    if paper is None:
        return
    changed = False
    for field in _PAPER_BACKFILL_FIELDS:
        new_value = detail.get(field)
        if not new_value:
            continue
        if not getattr(paper, field):
            setattr(paper, field, new_value)
            changed = True
    if changed:
        db.commit()


def _commit(db: Session) -> None:
    """提交;并发下撞 url_key / (pipe_id, external_id) 唯一约束时按已存在处理。"""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def refresh_repo(
    db: Session,
    doc_id: int,
    *,
    stars: int | None = None,
    forks: int | None = None,
    open_issues: int | None = None,
    pushed_at: int | None = None,
    stars_prev: int | None = None,
    stars_gained: int | None = None,
) -> None:
    """刷新仓库数据:repo.* 与 doc.sort_time / doc.last_modified_at 同步更新。

    只动 repo 与 doc 两张表,不触碰 analysis / membership 等判定结果。
    stars_prev / stars_gained 是相对上次刷新的增量快照,由刷新作业计算后传入;
    不传则保持原值(首次刷新两者皆为 NULL)。
    """
    now = _now()
    repo = db.get(Repo, doc_id)
    if repo is None:
        raise ValueError(f"repo {doc_id} 不存在")
    if stars is not None:
        repo.stars = stars
    if forks is not None:
        repo.forks = forks
    if open_issues is not None:
        repo.open_issues = open_issues
    if pushed_at is not None:
        repo.pushed_at = pushed_at
    if stars_prev is not None:
        repo.stars_prev = stars_prev
    if stars_gained is not None:
        repo.stars_gained = stars_gained
    repo.refreshed_at = now

    doc = db.get(Doc, doc_id)
    if pushed_at is not None:
        doc.sort_time = pushed_at
    doc.last_modified_at = now
    db.commit()


def check_orphans(db: Session) -> int:
    """孤儿 doc 计数:doc.kind 指向的实体表里没有对应行。

    孤儿意味着写入路径有 bug,只报告不清理(见 design 决策 6)。
    """
    total = 0
    for kind, model in _ENTITY_MODEL.items():
        total += (
            db.query(Doc)
            .filter(Doc.kind == kind)
            .outerjoin(model, model.id == Doc.id)
            .filter(model.id.is_(None))
            .count()
        )
    return total
