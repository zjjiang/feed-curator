"""服务端渲染页面。

阅读面(/ 订阅流、/docs/{id} 文档详情)与管理后台(/admin 概览、
/admin/pipes 渠道、/admin/domains 领域)分离;表单 POST 后 303 重定向。
与 JSON API(main.py)分离。
"""

import json
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.jobs.fetcher import fetch_source
from app.models import (Analysis, Article, Doc, Domain, Membership, Pipe,
                        Reading, Repo, RunLog)
from app.utils.json_str import json_dump

templates = Jinja2Templates(directory="app/web/templates")
router = APIRouter()

PER_PAGE = 50


def _fmt_time(ts: int | None) -> str:
    if not ts:
        return "-"
    dt = datetime.fromtimestamp(ts)
    diff = datetime.now() - dt
    if diff.days == 0:
        return dt.strftime("%H:%M")
    if diff.days < 7:
        return f"{diff.days}天前"
    return dt.strftime("%m-%d")


templates.env.filters["fmt_time"] = _fmt_time


# 旧路径重定向(书签兼容)
@router.get("/ops")
def ops_redirect():
    return RedirectResponse("/admin", status_code=307)


@router.get("/pipes")
def pipes_redirect():
    return RedirectResponse("/admin/pipes", status_code=307)


@router.get("/domains")
def domains_redirect():
    return RedirectResponse("/admin/domains", status_code=307)


def _domain_map(db: Session) -> dict[int, str]:
    return {d.id: d.name for d in db.query(Domain).all()}


# ============ 订阅(首页) ============


def _latest_ok_analysis_ids(db: Session):
    """每篇文档最新一条 ok 判定的 analysis.id。"""
    latest = (
        db.query(Analysis.doc_id, func.max(Analysis.id).label("aid"))
        .filter(Analysis.status == "ok")
        .group_by(Analysis.doc_id)
        .subquery()
    )
    return latest


@router.get("/", response_class=HTMLResponse)
def index_page(
    request: Request,
    page: int = 1,
    domain_id: int | None = None,
    kind: str | None = None,
    kind_tag: str | None = None,
    favorites: int = 0,
    sort: str = "time",
    db: Session = Depends(get_session),
):
    q = (
        db.query(Doc)
        .outerjoin(Reading, Reading.doc_id == Doc.id)
        .filter(func.coalesce(Reading.is_dismissed, 0) == 0)
    )
    if domain_id is not None:
        q = q.join(Membership, Membership.doc_id == Doc.id) \
            .filter(Membership.domain_id == domain_id)
    if kind:
        q = q.filter(Doc.kind == kind)
    if kind_tag:
        q = q.join(Article, Article.id == Doc.id) \
            .filter(Article.kind_tag == kind_tag)
    if favorites:
        q = q.filter(Reading.is_favorite == 1)

    if sort == "stars":
        latest = _latest_ok_analysis_ids(db)
        q = q.outerjoin(latest, latest.c.doc_id == Doc.id) \
            .outerjoin(Analysis, Analysis.id == latest.c.aid)
        q = q.order_by(func.coalesce(Analysis.stars, 0).desc(),
                       Doc.sort_time.desc(), Doc.id.desc())
    else:
        q = q.order_by(Doc.sort_time.desc(), Doc.id.desc())

    total = q.count()
    docs = q.offset((page - 1) * PER_PAGE).limit(PER_PAGE).all()
    rows = _decorate_docs(db, docs)
    dmap = _domain_map(db)

    qs_params = [("domain_id", domain_id), ("kind", kind),
                 ("kind_tag", kind_tag), ("sort", sort if sort != "time" else None),
                 ("favorites", favorites or None)]
    qs_prefix = "&".join(f"{k}={v}" for k, v in qs_params if v is not None)

    return templates.TemplateResponse(request, "index.html", {
        "docs": rows,
        "domains": db.query(Domain).order_by(Domain.id).all(),
        "domain_id": domain_id,
        "domain_name": dmap.get(domain_id),
        "kind": kind,
        "kind_tag": kind_tag,
        "favorites": favorites,
        "sort": sort,
        "total": total,
        "page": page,
        "qs_prefix": qs_prefix,
        "total_pages": max(1, (total + PER_PAGE - 1) // PER_PAGE),
    })


def _decorate_docs(db: Session, docs: list[Doc]) -> list[dict]:
    doc_ids = [d.id for d in docs]
    if not doc_ids:
        return []

    analyses = {
        a.doc_id: a
        for a in db.query(Analysis)
        .filter(Analysis.doc_id.in_(doc_ids), Analysis.status == "ok")
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
        .all()
    }
    readings = {
        r.doc_id: r for r in db.query(Reading).filter(Reading.doc_id.in_(doc_ids)).all()
    }
    memberships: dict[int, list[str]] = {}
    dmap = _domain_map(db)
    for m in db.query(Membership).filter(Membership.doc_id.in_(doc_ids)).all():
        memberships.setdefault(m.doc_id, []).append(dmap.get(m.domain_id, "?"))
    kind_tags = {
        a.id: a.kind_tag
        for a in db.query(Article).filter(Article.id.in_(doc_ids)).all()
    }
    repos = {
        r.id: r for r in db.query(Repo).filter(Repo.id.in_(doc_ids)).all()
    }

    rows = []
    for d in docs:
        a = analyses.get(d.id)
        r = readings.get(d.id)
        repo = repos.get(d.id) if d.kind == "repo" else None
        rows.append({
            "id": d.id, "kind": d.kind, "title": d.title, "url": d.url,
            "time_fmt": _fmt_time(d.sort_time),
            "kind_tag": kind_tags.get(d.id),
            "domains": memberships.get(d.id, []),
            "stars": a.stars if a else None,
            "summary": a.summary if a else None,
            "is_read": bool(r.is_read) if r else False,
            "is_favorite": bool(r.is_favorite) if r else False,
            "gh_stars": repo.stars if repo else None,
            "gh_stars_gained": repo.stars_gained if repo else None,
            "language": repo.language if repo else None,
        })
    return rows


@router.get("/docs/{doc_id}", response_class=HTMLResponse)
def doc_page(request: Request, doc_id: int, db: Session = Depends(get_session)):
    doc = db.get(Doc, doc_id)
    if not doc:
        return HTMLResponse("文档不存在", status_code=404)

    from app.models import Paper

    entity_model = {"paper": Paper, "repo": Repo, "article": Article}[doc.kind]
    entity = db.get(entity_model, doc_id)

    content_field = {"paper": "abstract", "repo": "readme_text",
                     "article": "content_text"}[doc.kind]
    content = (getattr(entity, content_field) or "") if entity else ""

    analysis = (
        db.query(Analysis)
        .filter(Analysis.doc_id == doc_id, Analysis.status == "ok")
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
        .first()
    )
    keypoints = json.loads(analysis.keypoints) if analysis and analysis.keypoints else []
    domains = [m for m in db.query(Membership).filter_by(doc_id=doc_id).all()]
    dmap = _domain_map(db)
    reading = db.get(Reading, doc_id)

    word_count = getattr(entity, "word_count", None) if entity else None

    return templates.TemplateResponse(request, "doc.html", {
        "doc": doc,
        "kind_label": {"paper": "论文", "repo": "项目", "article": "文章"}[doc.kind],
        "kind_tag": getattr(entity, "kind_tag", None) if entity else None,
        "meta": entity,
        "content": content,
        "analysis": analysis,
        "keypoints": keypoints,
        "domain_names": [dmap.get(m.domain_id, "?") for m in domains],
        "reading": reading,
        "word_count": word_count,
        "time_fmt": _fmt_time(doc.sort_time),
    })


# ============ 阅读状态操作 ============


def _apply_reading(db: Session, doc_id: int, **fields) -> None:
    reading = db.get(Reading, doc_id)
    if reading is None:
        reading = Reading(doc_id=doc_id, updated_at=int(time.time()))
        db.add(reading)
    for key, value in fields.items():
        setattr(reading, key, value)
    reading.updated_at = int(time.time())
    db.commit()


@router.post("/docs/{doc_id}/read")
def mark_read_page(doc_id: int, request: Request, db: Session = Depends(get_session)):
    _apply_reading(db, doc_id, is_read=1)
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


@router.post("/docs/{doc_id}/favorite")
def toggle_favorite_page(doc_id: int, request: Request,
                         db: Session = Depends(get_session)):
    reading = db.get(Reading, doc_id)
    current = bool(reading.is_favorite) if reading else False
    _apply_reading(db, doc_id, is_favorite=0 if current else 1)
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


@router.post("/docs/{doc_id}/dismiss")
def dismiss_page(doc_id: int, request: Request, db: Session = Depends(get_session)):
    _apply_reading(db, doc_id, is_dismissed=1)
    return RedirectResponse("/", status_code=303)


@router.post("/docs/{doc_id}/rating")
def rating_page(doc_id: int, request: Request, rating: int = Form(...),
                db: Session = Depends(get_session)):
    if 1 <= rating <= 5:
        _apply_reading(db, doc_id, rating=rating)
    return RedirectResponse(f"/docs/{doc_id}", status_code=303)


@router.post("/docs/{doc_id}/note")
def note_page(doc_id: int, request: Request, note: str = Form(""),
              db: Session = Depends(get_session)):
    _apply_reading(db, doc_id, note=note.strip() or None)
    return RedirectResponse(f"/docs/{doc_id}", status_code=303)


# ============ 管理后台:领域 ============


@router.get("/admin/domains", response_class=HTMLResponse)
def domains_page(request: Request, db: Session = Depends(get_session)):
    domains = []
    counts = dict(
        db.query(Membership.domain_id, func.count(Membership.doc_id))
        .group_by(Membership.domain_id).all()
    )
    for d in db.query(Domain).order_by(Domain.id).all():
        domains.append({
            "id": d.id, "name": d.name, "description": d.description,
            "keywords_str": "、".join(json.loads(d.keywords)) if d.keywords else "",
            "enabled": bool(d.enabled),
            "doc_count": counts.get(d.id, 0),
        })
    return templates.TemplateResponse(request, "domains.html", {"domains": domains})


@router.post("/admin/domains/add")
async def add_domain_page(request: Request, db: Session = Depends(get_session)):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return RedirectResponse("/admin/domains", status_code=303)
    if db.query(Domain).filter(Domain.name == name).first():
        return RedirectResponse("/admin/domains?error=dup", status_code=303)
    now = int(time.time())
    keywords = [k.strip() for k in (form.get("keywords") or "").replace("，", ",").split(",") if k.strip()]
    db.add(Domain(name=name, description=(form.get("description") or "").strip(),
                  keywords=json_dump(keywords), enabled=1,
                  created_at=now, updated_at=now))
    db.commit()
    return RedirectResponse("/admin/domains", status_code=303)


@router.post("/admin/domains/{domain_id}/update")
async def update_domain_page(domain_id: int, request: Request,
                             db: Session = Depends(get_session)):
    form = await request.form()
    d = db.get(Domain, domain_id)
    if d:
        if form.get("description") is not None:
            d.description = (form.get("description") or "").strip()
        if form.get("keywords") is not None:
            keywords = [k.strip() for k in
                        (form.get("keywords") or "").replace("，", ",").split(",")
                        if k.strip()]
            d.keywords = json_dump(keywords)
        if form.get("toggle_enabled"):
            d.enabled = 0 if d.enabled else 1
        d.updated_at = int(time.time())
        db.commit()
    return RedirectResponse("/admin/domains", status_code=303)


# ============ 管理后台:渠道 ============


@router.get("/admin/pipes", response_class=HTMLResponse)
def pipes_page(request: Request, db: Session = Depends(get_session)):
    dmap = _domain_map(db)
    pipes = []
    for p in db.query(Pipe).order_by(Pipe.id).all():
        pipes.append({
            "id": p.id, "type": p.type, "name": p.name,
            "domain_name": dmap.get(p.domain_id) if p.domain_id else None,
            "is_derived": p.domain_id is not None,
            "enabled": bool(p.enabled),
            "fetch_interval_min": p.fetch_interval_min,
            "last_fetched_fmt": _fmt_time(p.last_fetched_at),
            "last_error": p.last_error,
            "type_supported": p.type in ("rss", "arxiv", "wechat", "github",
                                         "hf_papers", "manual"),
        })
    return templates.TemplateResponse(request, "pipes.html", {
        "pipes": pipes,
        "domains": db.query(Domain).filter(Domain.enabled == 1).all(),
    })


@router.post("/admin/pipes/add")
async def add_pipe_page(request: Request, db: Session = Depends(get_session)):
    from app.services.source_service import create_pipe

    form = await request.form()
    pipe_type = form.get("type") or "rss"
    name = (form.get("name") or "").strip()
    value = (form.get("config_value") or "").strip()
    interval = int(form.get("interval") or 30)
    domain_id = form.get("domain_id") or None
    domain_id = int(domain_id) if domain_id else None

    if pipe_type == "rss":
        config = {"feed_url": value}
    elif pipe_type == "arxiv":
        config = {"category": value or "cs.AI", "max_results": 30}
    elif pipe_type == "github":
        config = {}
        if value:
            config["query"] = value
        for key in ("window_days", "min_stars", "per_page"):
            raw = (form.get(key) or "").strip()
            if raw:
                config[key] = int(raw)
    elif pipe_type == "hf_papers":
        config = {}
        if value:
            config["base_url"] = value
    else:
        config = {"mp_id": value, "wewe_base_url": "http://localhost:9001"}

    existing_feed = None
    if pipe_type == "rss":
        shared = db.query(Pipe).filter(
            Pipe.type == "rss", Pipe.domain_id.is_(None)).all()
        for s in shared:
            cfg = json.loads(s.config) if s.config else {}
            if cfg.get("feed_url") == value:
                existing_feed = s
                break
    if existing_feed:
        return RedirectResponse(
            f"/admin/pipes?error=shared_exists&existing={existing_feed.id}", status_code=303)

    if name:
        try:
            create_pipe(db, pipe_type, name, config, interval, domain_id)
        except ValueError as e:
            from urllib.parse import quote
            return RedirectResponse(
                f"/admin/pipes?error=create&msg={quote(str(e))}", status_code=303)
    return RedirectResponse("/admin/pipes", status_code=303)


@router.post("/admin/pipes/{pipe_id}/toggle")
def toggle_pipe_page(pipe_id: int, db: Session = Depends(get_session)):
    pipe = db.get(Pipe, pipe_id)
    if pipe:
        pipe.enabled = 0 if pipe.enabled else 1
        pipe.updated_at = int(time.time())
        db.commit()
    return RedirectResponse("/admin/pipes", status_code=303)


@router.get("/admin/pipes/{pipe_id}/fetch")
def fetch_pipe_page(pipe_id: int, db: Session = Depends(get_session)):
    pipe = db.get(Pipe, pipe_id)
    if pipe and pipe.type != "manual":
        fetch_source(db, pipe, trigger="manual")
    return RedirectResponse("/admin/pipes", status_code=303)


@router.post("/admin/pipes/save-url")
async def save_url_page(request: Request, db: Session = Depends(get_session)):
    from app.services import manual_service
    from app.services.fulltext import ArchiveError

    form = await request.form()
    url = (form.get("url") or "").strip()
    try:
        manual_service.save_url(db, url)
    except ArchiveError as e:
        from urllib.parse import quote
        return RedirectResponse(f"/admin/pipes?error=save_url&msg={quote(str(e))}",
                                status_code=303)
    return RedirectResponse("/admin/pipes?saved=1", status_code=303)


# ============ 管理后台:仓库维护 ============


@router.post("/admin/repos/refresh")
def trigger_repo_refresh():
    """手动触发一轮星标刷新(maybe_start_refresh 自带单飞与到期检查)。"""
    from app.services.repo_refresh import maybe_start_refresh

    started = maybe_start_refresh(trigger="manual")
    msg = "refresh_started" if started else "refresh_busy"
    return RedirectResponse(f"/admin?msg={msg}", status_code=303)


_readme_running = None    # README 补抓线程句柄(进程内单例,与 run_log 双保险)


@router.post("/admin/repos/readme")
def trigger_readme_backfill(db: Session = Depends(get_session)):
    """手动补抓一批缺失的 README(限速在服务内,1 秒/个)。"""
    global _readme_running
    import threading

    running = (
        db.query(RunLog)
        .filter(RunLog.kind == "readme", RunLog.status == "running")
        .first()
    )
    if running or (_readme_running is not None and _readme_running.is_alive()):
        return RedirectResponse("/admin?msg=readme_busy", status_code=303)

    from app.services.repo_enrich import run_readme_backfill

    done = threading.Event()

    def worker():
        try:
            run_readme_backfill(sleep_seconds=1.0)
        finally:
            done.set()

    _readme_running = threading.Thread(target=worker, daemon=True)
    _readme_running.start()
    return RedirectResponse("/admin?msg=readme_started", status_code=303)


# ============ 管理后台 ============


@router.get("/admin", response_class=HTMLResponse)
def ops_page(request: Request, db: Session = Depends(get_session)):
    from app.services.fulltext_backfill import FULLTEXT_MIN_WORDS
    from app.ai.analyzer import select_doc_ids

    now = int(time.time())
    day_ago = now - 86400

    runs = db.query(RunLog).order_by(RunLog.id.desc()).limit(50).all()
    run_rows = [{
        "id": r.id, "kind": r.kind, "pipe_name": r.pipe_name or "-",
        "trigger": r.trigger, "status": r.status,
        "counts": _run_counts(r), "error": r.error,
        "duration": _fmt_duration(r.duration_ms),
        "created_fmt": _fmt_time(r.created_at),
    } for r in runs]

    runs_24h = db.query(RunLog).filter(RunLog.created_at >= day_ago).all()
    fetch_runs = [r for r in runs_24h if r.kind == "fetch"]

    by_kind = dict(db.query(Doc.kind, func.count(Doc.id)).group_by(Doc.kind).all())
    articles = db.query(Article).count()
    sufficient = db.query(Article).filter(
        Article.word_count >= FULLTEXT_MIN_WORDS).count()

    from app.writer import check_orphans

    pending_analyze = len(select_doc_ids(db))

    running = db.query(RunLog).filter(RunLog.status == "running") \
        .order_by(RunLog.id.desc()).all()

    summary = {
        "docs_total": sum(by_kind.values()),
        "papers": by_kind.get("paper", 0),
        "repos": by_kind.get("repo", 0),
        "articles": by_kind.get("article", 0),
        "fulltext": f"{sufficient}/{articles}",
        "pending_analyze": pending_analyze,
        "fetch_ok_24h": sum(1 for r in fetch_runs if r.status == "done"),
        "fetch_fail_24h": sum(1 for r in fetch_runs if r.status != "done"),
        "orphan_docs": check_orphans(db),
        "running": running,
    }

    return templates.TemplateResponse(request, "ops.html", {
        "summary": summary,
        "run_rows": run_rows,
    })


def _run_counts(r: RunLog) -> str:
    if r.kind == "fetch":
        return f"+{r.inserted}"
    if r.kind in ("fulltext", "readme", "refresh"):
        return f"{r.processed}/{r.total}"
    return f"{r.processed}/{r.total} (成{r.succeeded}/败{r.failed})"


def _fmt_duration(ms: int | None) -> str:
    if not ms:
        return "-"
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms // 1000}s"
