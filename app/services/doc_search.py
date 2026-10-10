"""检索服务:面向 agent 的 search_docs / get_doc。

search_docs = search_index 的 BM25 召回 + MySQL 过滤/补全/片段:
召回在索引侧(top-K 超采样),过滤在库侧(kind / days / domain),两者
AND 组合;判定信息只做附带展示,判定缺失不排除文档。
get_doc 按 doc_id 取全文与最新成功判定,供 agent 对候选结果深读。
"""

import json
import time

from app.models import Analysis, Domain, Doc, Membership
from app.services import search_index

_KINDS = ("article", "paper", "repo")
_LIMIT_CAP = 50
_SNIPPET_WINDOW = 120  # 命中位置前后各取的字符数
_EMPTY_NOTE = ("没有命中任何文档;可尝试更换或减少关键词、放宽过滤条件"
               "(扩大 days、去掉 kind/domain)。")


# ============ 内容路由 ============


def _content_of(db, doc: Doc) -> str:
    """实体全文:get_doc 的内容来源(与索引预览不同,这里不截断)。"""
    from app.models import Article, Paper, Repo

    if doc.kind == "article":
        a = db.get(Article, doc.id)
        return (a.content_text if a is not None else "") or ""
    if doc.kind == "paper":
        p = db.get(Paper, doc.id)
        return (p.abstract if p is not None else "") or ""
    if doc.kind == "repo":
        r = db.get(Repo, doc.id)
        if r is None:
            return ""
        return r.readme_text or r.description or ""
    return ""


def _make_snippet(content: str, tokens: list[str]) -> str:
    """围绕首个命中词切窗口;命中不在正文(如仅标题命中)回退正文开头。"""
    if not content:
        return ""
    low = content.lower()
    pos = -1
    for tok in tokens:
        pos = low.find(tok.lower())
        if pos >= 0:
            break
    if pos < 0:  # 命中来自标题/预览截断之外,回退开头
        return content[: _SNIPPET_WINDOW * 2]
    start = max(0, pos - _SNIPPET_WINDOW)
    end = min(len(content), pos + _SNIPPET_WINDOW)
    part = content[start:end]
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{part}{suffix}"


def _latest_ok_analysis(db, doc_ids: list[int]) -> dict[int, Analysis]:
    if not doc_ids:
        return {}
    latest = (
        db.query(Analysis.doc_id, __import__("sqlalchemy").func.max(Analysis.id).label("aid"))
        .filter(Analysis.status == "ok", Analysis.doc_id.in_(doc_ids))
        .group_by(Analysis.doc_id)
        .subquery()
    )
    rows = (
        db.query(Analysis)
        .join(latest, latest.c.aid == Analysis.id)
        .all()
    )
    return {a.doc_id: a for a in rows}


def _loads(value: str | None) -> list:
    return json.loads(value) if value else []


# ============ search_docs ============


def search_docs(db, query: str, *, kind: str | None = None, domain: str | None = None,
                days: int | None = None, limit: int = 10,
                index_dir: str | None = None) -> dict:
    """关键词检索。返回 {ok, count, results, note?};参数错误返回 {ok: False, error}。"""
    if not (query or "").strip():
        return {"ok": False, "error": "query 不能为空"}
    if kind is not None and kind not in _KINDS:
        return {"ok": False, "error": f"kind 必须是 {'/'.join(_KINDS)} 之一"}
    if days is not None and days < 1:
        return {"ok": False, "error": "days 必须 >= 1"}
    limit = max(1, min(int(limit), _LIMIT_CAP))

    idx = search_index.maybe_rebuild(db, index_dir or search_index.DEFAULT_INDEX_DIR)
    # 超采样再过滤:库侧过滤会淘汰部分召回,3 倍余量在 3k 语料下足够
    hits = idx.search(query, top_k=limit * 3)
    if not hits:
        return {"ok": True, "count": 0, "results": [], "note": _EMPTY_NOTE}

    since = int(time.time()) - days * 86400 if days else None
    results: list[dict] = []
    analysis_map = _latest_ok_analysis(db, [doc_id for doc_id, _ in hits])
    for doc_id, _score in hits:
        if len(results) >= limit:
            break
        doc = db.get(Doc, doc_id)
        if doc is None or (kind is not None and doc.kind != kind):
            continue
        if since is not None and (doc.sort_time is None or doc.sort_time < since):
            continue
        if domain is not None and not _in_domain(db, doc_id, domain):
            continue
        a = analysis_map.get(doc_id)
        results.append({
            "doc_id": doc.id,
            "kind": doc.kind,
            "title": doc.title,
            "url": doc.url,
            "snippet": _make_snippet(_content_of(db, doc), search_index.tokenize_query(query)),
            "stars": a.stars if a is not None else None,
            "domains": _loads(a.domains) if a is not None else [],
            "sort_time": _fmt(doc.sort_time),
        })

    out = {"ok": True, "count": len(results), "results": results}
    if not results:
        out["note"] = "命中被过滤条件全部排除;可放宽 kind/days/domain。"
    return out


def _in_domain(db, doc_id: int, domain_name: str) -> bool:
    return (
        db.query(Membership)
        .join(Domain, Domain.id == Membership.domain_id)
        .filter(Membership.doc_id == doc_id, Domain.name == domain_name)
        .first()
        is not None
    )


def _fmt(ts: int | None) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


# ============ get_doc ============


def get_doc(db, doc_id: int, *, max_chars: int = 8000,
            index_dir: str | None = None) -> dict:
    """按 doc_id 取标题/URL/全文与最新成功判定;文档不存在返回失败。"""
    doc = db.get(Doc, int(doc_id))
    if doc is None:
        return {"ok": False, "error": f"文档 {doc_id} 不存在"}

    content = _content_of(db, doc)
    total = len(content)
    truncated = total > max_chars
    a = _latest_ok_analysis(db, [doc.id]).get(doc.id)
    return {
        "ok": True,
        "doc_id": doc.id,
        "kind": doc.kind,
        "title": doc.title,
        "url": doc.url,
        "content": content[:max_chars],
        "content_truncated": truncated,
        "content_total_chars": total,
        "analysis": None if a is None else {
            "summary": a.summary or "",
            "keypoints": _loads(a.keypoints),
            "domains": _loads(a.domains),
            "stars": a.stars,
        },
    }
