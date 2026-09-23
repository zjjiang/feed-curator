"""判定编排:选文档 → 调 LLM → 写 analysis(追加) → 物化 membership 与 kind_tag。

analysis 是真相来源、永不 UPDATE;membership 与 article.kind_tag 是其物化结果,
由本模块统一维护(design 决策 5)。重判只删改 assigned_by='ai' 的归属,
manual 记录永不被自动删除。
"""

import json
import time

from sqlalchemy.orm import Session

from app.ai.client import PROMPT_VERSION, LLMClient
from app.models import Analysis, Article, Domain, Doc, Membership, Paper, Repo
from app.utils.json_str import json_dump

_CONTENT_FIELD = {"paper": "abstract", "repo": "readme_text", "article": "content_text"}


def _now() -> int:
    return int(time.time())


def load_domains(db: Session) -> list[dict]:
    """启用中的领域表,喂给 LLM 的候选集。"""
    out = []
    for d in db.query(Domain).filter(Domain.enabled == 1).all():
        out.append({
            "name": d.name,
            "description": d.description or "",
            "keywords": json.loads(d.keywords) if d.keywords else [],
        })
    return out


def select_doc_ids(db: Session, *, force_all: bool = False) -> list[int]:
    """待判定文档:有内容,且(无成功判定 或 上次失败;force_all 则全部)。

    内容为空不判定——等正文补全后再来。
    """
    rows = (
        db.query(Doc.id, Doc.kind, Paper.abstract, Repo.readme_text, Article.content_text)
        .outerjoin(Paper, Paper.id == Doc.id)
        .outerjoin(Repo, Repo.id == Doc.id)
        .outerjoin(Article, Article.id == Doc.id)
        .all()
    )
    ok_ids = {r[0] for r in db.query(Analysis.doc_id).filter(Analysis.status == "ok").all()}
    out = []
    for doc_id, kind, abstract, readme, content in rows:
        if not force_all and doc_id in ok_ids:
            continue    # 已成功判定不自动重判;失败的天然不在 ok 集里,可重试
        text = {"paper": abstract, "repo": readme, "article": content}[kind] or ""
        if not text.strip():
            continue
        out.append(doc_id)
    return sorted(out)


def _entity_input(db: Session, doc: Doc) -> tuple[str, str, bool]:
    """(description, content_preview, content_sufficient),按实体类型取。"""
    if doc.kind == "paper":
        paper = db.get(Paper, doc.id)
        return (paper.abstract or "")[:200], paper.abstract or "", True
    if doc.kind == "repo":
        repo = db.get(Repo, doc.id)
        return (repo.description or "")[:200], repo.readme_text or "", True
    article = db.get(Article, doc.id)
    sufficient = (article.word_count or 0) >= 500
    return (article.description or "")[:200], article.content_text or "", sufficient


def analyze_doc(
    db: Session,
    llm: LLMClient,
    doc_id: int,
    domains: list[dict],
) -> bool:
    """判定单篇:追加写 analysis,成功时物化。返回是否成功。"""
    doc = db.get(Doc, doc_id)
    if doc is None:
        return False
    description, content_preview, sufficient = _entity_input(db, doc)

    result = llm.analyze(
        kind=doc.kind,
        title=doc.title,
        description=description,
        content_preview=content_preview,
        content_sufficient=sufficient,
        domains=domains,
    )
    now = _now()
    if result is None:
        db.add(Analysis(doc_id=doc_id, status="failed", model=llm.model,
                        prompt_version=PROMPT_VERSION,
                        error="LLM 输出无法解析或星级缺失", created_at=now))
        db.commit()
        return False

    db.add(Analysis(
        doc_id=doc_id, status="ok", model=llm.model, prompt_version=PROMPT_VERSION,
        summary=result["summary"], keypoints=json_dump(result["keypoints"]),
        domains=json_dump(result["domains"]), article_kind=result["article_kind"],
        stars=result["stars"], created_at=now,
    ))
    _materialize(db, doc, result)
    db.commit()
    return True


def _materialize(db: Session, doc: Doc, result: dict) -> None:
    """把判定结果落到 membership(ai 来源)与 article.kind_tag。"""
    names = result["domains"]
    domain_ids: set[int] = set()
    if names:
        rows = db.query(Domain.id, Domain.name).filter(Domain.name.in_(names)).all()
        domain_ids = {d_id for d_id, _ in rows}

    existing_ai = {
        m.domain_id
        for m in db.query(Membership).filter_by(doc_id=doc.id, assigned_by="ai").all()
    }
    for d_id in existing_ai - domain_ids:
        db.query(Membership).filter_by(doc_id=doc.id, domain_id=d_id,
                                       assigned_by="ai").delete()
    for d_id in domain_ids - existing_ai:
        db.add(Membership(doc_id=doc.id, domain_id=d_id, assigned_by="ai",
                          created_at=_now()))

    if doc.kind == "article":
        article = db.get(Article, doc.id)
        article.kind_tag = result["article_kind"]
