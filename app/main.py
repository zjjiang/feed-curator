import json
import os
import time
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.adapters import ADAPTERS
from app.ai import analyzer
from app.db import SessionLocal, get_session, init_db
from app.jobs.fetcher import fetch_source
from app.jobs.runner import cancel_analyze, start_analyze_job
from app.models import (Analysis, Article, Discovery, Doc, Domain, DocumentLink,
                        Membership, Paper, Pipe, Reading, Repo, RunLog)
from app.utils.json_str import json_dump
from app.web.pages import router as web_router
from app.mcp_server import mcp


scheduler = BackgroundScheduler()
_llm_client = None


def _get_llm():
    global _llm_client
    if _llm_client is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            return None
        from app.ai.client import LLMClient
        _llm_client = LLMClient(api_key=api_key)
    return _llm_client


def _run_fetch_cycle():
    """每分钟检查:到期的启用管道逐一采集。单管道失败不影响其他管道。"""
    now = int(time.time())
    db = SessionLocal()
    try:
        pipes = (
            db.query(Pipe)
            .filter(Pipe.enabled == 1, Pipe.type.in_(ADAPTERS.keys()))
            .all()
        )
        for pipe in pipes:
            interval = (pipe.fetch_interval_min or 30) * 60
            last = pipe.last_fetched_at or 0
            if now - last < interval:
                continue
            try:
                count, err = fetch_source(db, pipe)
                if err:
                    print(f"[fetch] {pipe.name} 失败: {err}")
                elif count > 0:
                    print(f"[fetch] {pipe.name} 新增 {count} 条")
            except Exception as e:  # noqa: BLE001 — 管道间故障隔离
                db.rollback()
                print(f"[fetch] {pipe.name} 异常: {type(e).__name__}: {e}")
    finally:
        db.close()


def _run_analyze_cycle():
    """有待判定文档且没有任务在跑时,自动建一个判定任务。无 key 则空转。"""
    if not _get_llm():
        return
    run_id, created = start_analyze_job(trigger="auto")
    if created:
        print(f"[ai] 自动创建判定任务 #{run_id}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.add_job(_run_fetch_cycle, "interval", seconds=60, id="fetcher")
    scheduler.add_job(_run_analyze_cycle, "interval", seconds=300, id="analyzer")
    scheduler.start()
    llm_status = "已配置" if _get_llm() else "未配置 DEEPSEEK_API_KEY"
    print(f"[feed-curator] 启动完成，调度器已运行，AI判定: {llm_status}")
    # 挂载式 streamable-http 必须在父应用 lifespan 内启动其 session manager，
    # 否则 /mcp 端点不工作。
    async with mcp.session_manager.run():
        print("[feed-curator] MCP server 已挂载于 /mcp")
        yield
    scheduler.shutdown()


app = FastAPI(title="feed-curator", lifespan=lifespan)
app.include_router(web_router)
app.mount("/mcp", mcp.streamable_http_app())


@app.get("/health")
def health():
    return {"status": "ok"}


# ============ 管道 ============


class PipeCreate(BaseModel):
    type: str
    name: str
    config: dict
    fetch_interval_min: int = 30
    domain_id: int | None = None


@app.post("/api/pipes")
def create_pipe_api(body: PipeCreate, db: Session = Depends(get_session)):
    from app.services.source_service import create_pipe

    pipe = create_pipe(db, body.type, body.name, body.config,
                       body.fetch_interval_min, body.domain_id)
    return {"id": pipe.id, "name": pipe.name, "type": pipe.type}


@app.get("/api/pipes")
def list_pipes_api(db: Session = Depends(get_session)):
    pipes = db.query(Pipe).order_by(Pipe.created_at.desc()).all()
    return [
        {"id": p.id, "type": p.type, "name": p.name, "enabled": bool(p.enabled),
         "domain_id": p.domain_id, "fetch_interval_min": p.fetch_interval_min,
         "last_fetched_at": p.last_fetched_at, "last_error": p.last_error}
        for p in pipes
    ]


@app.post("/api/pipes/{pipe_id}/fetch")
def trigger_fetch(pipe_id: int, db: Session = Depends(get_session)):
    pipe = db.query(Pipe).filter(Pipe.id == pipe_id).first()
    if not pipe:
        raise HTTPException(404, "pipe not found")
    count, err = fetch_source(db, pipe, trigger="manual")
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "inserted": count}


# ============ 手工存入 ============


class SaveUrlBody(BaseModel):
    url: str
    note: str | None = None


@app.post("/api/docs/save-url")
def save_url_api(body: SaveUrlBody, db: Session = Depends(get_session)):
    from app.services import manual_service

    try:
        return manual_service.save_url(db, body.url, note=body.note)
    except Exception as e:  # noqa: BLE001 — 工具/表单边界,转友好错误
        raise HTTPException(400, str(e))


# ============ 文档与领域视图 ============


@app.get("/api/docs")
def list_docs_api(
    domain_id: int | None = Query(None),
    kind: str | None = Query(None),
    kind_tag: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
):
    query, _ = _docs_query(db, domain_id=domain_id, kind=kind, kind_tag=kind_tag)
    total = query.count()
    # sort_time 可空;DESC 下 SQLite 与 MySQL 都把 NULL 排在末尾,无需 NULLS LAST
    docs = query.order_by(Doc.sort_time.desc(), Doc.id.desc()) \
        .offset(offset).limit(limit).all()
    return {"total": total, "docs": [_doc_dict(db, d) for d in docs]}


def _docs_query(db: Session, *, domain_id=None, kind=None, kind_tag=None):
    query = db.query(Doc)
    query = query.outerjoin(Reading, Reading.doc_id == Doc.id) \
        .filter(func.coalesce(Reading.is_dismissed, 0) == 0)
    if domain_id is not None:
        query = query.join(Membership, Membership.doc_id == Doc.id) \
            .filter(Membership.domain_id == domain_id)
    if kind:
        query = query.filter(Doc.kind == kind)
    if kind_tag:
        query = query.join(Article, Article.id == Doc.id) \
            .filter(Article.kind_tag == kind_tag)
    return query, None


def _latest_analysis(db: Session, doc_id: int) -> Analysis | None:
    return (
        db.query(Analysis)
        .filter(Analysis.doc_id == doc_id, Analysis.status == "ok")
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
        .first()
    )


def _doc_dict(db: Session, doc: Doc) -> dict:
    analysis = _latest_analysis(db, doc.id)
    reading = db.get(Reading, doc.id)
    domains = [
        row.name
        for row in db.query(Domain.name)
        .join(Membership, Membership.domain_id == Domain.id)
        .filter(Membership.doc_id == doc.id)
        .all()
    ]
    kind_tag = None
    if doc.kind == "article":
        article = db.get(Article, doc.id)
        kind_tag = article.kind_tag if article else None
    return {
        "id": doc.id,
        "kind": doc.kind,
        "title": doc.title,
        "url": doc.url,
        "sort_time": doc.sort_time,
        "kind_tag": kind_tag,
        "domains": domains,
        "stars": analysis.stars if analysis else None,
        "summary": analysis.summary if analysis else None,
        "is_read": bool(reading.is_read) if reading else False,
        "is_favorite": bool(reading.is_favorite) if reading else False,
    }


@app.get("/api/docs/{doc_id}")
def get_doc_api(doc_id: int, db: Session = Depends(get_session)):
    doc = db.get(Doc, doc_id)
    if not doc:
        raise HTTPException(404, "doc not found")
    out = _doc_dict(db, doc)
    analysis = _latest_analysis(db, doc_id)
    if analysis:
        out["keypoints"] = json.loads(analysis.keypoints) if analysis.keypoints else []
    entity = {"paper": Paper, "repo": Repo, "article": Article}[doc.kind]
    row = db.get(entity, doc_id)
    content_field = {"paper": "abstract", "repo": "readme_text",
                     "article": "content_text"}[doc.kind]
    out["content_text"] = (getattr(row, content_field) or "") if row else ""
    return out


# ============ 领域 ============


class DomainBody(BaseModel):
    name: str
    description: str = ""
    keywords: list[str] = []
    enabled: bool = True


@app.post("/api/domains")
def create_domain(body: DomainBody, db: Session = Depends(get_session)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "领域名不能为空")
    if db.query(Domain).filter(Domain.name == name).first():
        raise HTTPException(409, "领域名已存在")
    now = int(time.time())
    d = Domain(name=name, description=body.description.strip(),
               keywords=json_dump(body.keywords), enabled=int(body.enabled),
               created_at=now, updated_at=now)
    db.add(d)
    db.commit()
    return {"id": d.id, "name": d.name}


@app.get("/api/domains")
def list_domains(db: Session = Depends(get_session)):
    domains = db.query(Domain).order_by(Domain.id).all()
    return [
        {"id": d.id, "name": d.name, "description": d.description,
         "keywords": json.loads(d.keywords) if d.keywords else [],
         "enabled": bool(d.enabled)}
        for d in domains
    ]


class DomainUpdate(BaseModel):
    description: str | None = None
    keywords: list[str] | None = None
    enabled: bool | None = None


@app.patch("/api/domains/{domain_id}")
def update_domain(domain_id: int, body: DomainUpdate,
                  db: Session = Depends(get_session)):
    d = db.get(Domain, domain_id)
    if not d:
        raise HTTPException(404, "domain not found")
    if body.description is not None:
        d.description = body.description.strip()
    if body.keywords is not None:
        d.keywords = json_dump([k.strip() for k in body.keywords if k.strip()])
    if body.enabled is not None:
        d.enabled = int(body.enabled)
    d.updated_at = int(time.time())
    db.commit()
    return {"ok": True}


# ============ 阅读状态 ============


class ReadingBody(BaseModel):
    is_read: bool | None = None
    is_favorite: bool | None = None
    is_dismissed: bool | None = None
    rating: int | None = None
    note: str | None = None


@app.post("/api/docs/{doc_id}/reading")
def set_reading(doc_id: int, body: ReadingBody, db: Session = Depends(get_session)):
    if not db.get(Doc, doc_id):
        raise HTTPException(404, "doc not found")
    reading = db.get(Reading, doc_id)
    if reading is None:
        reading = Reading(doc_id=doc_id, updated_at=int(time.time()))
        db.add(reading)
    if body.is_read is not None:
        reading.is_read = int(body.is_read)
    if body.is_favorite is not None:
        reading.is_favorite = int(body.is_favorite)
    if body.is_dismissed is not None:
        reading.is_dismissed = int(body.is_dismissed)
    if body.rating is not None:
        if not 1 <= body.rating <= 5:
            raise HTTPException(400, "评分须在 1-5 之间")
        reading.rating = body.rating
    if body.note is not None:
        reading.note = body.note
    reading.updated_at = int(time.time())
    db.commit()
    return {"ok": True}


# ============ 判定任务 ============


@app.post("/api/analyze/run")
def start_analyze(force: bool = False):
    if not _get_llm():
        return {"ok": False, "error": "DEEPSEEK_API_KEY 未配置"}
    run_id, created = start_analyze_job(trigger="manual", force_all=force)
    if run_id is None:
        return {"ok": True, "run_id": None, "msg": "没有待判定的文档"}
    return {"ok": True, "run_id": run_id, "created": created,
            "msg": "已创建判定任务" if created else "已有任务在运行,已复用"}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: int):
    ok = cancel_analyze(run_id)
    return {"ok": ok, "msg": "已请求取消" if ok else "任务不在运行中"}


@app.get("/api/runs")
def list_runs(kind: str | None = Query(None),
              limit: int = Query(30, ge=1, le=200),
              db: Session = Depends(get_session)):
    q = db.query(RunLog)
    if kind:
        q = q.filter(RunLog.kind == kind)
    runs = q.order_by(RunLog.id.desc()).limit(limit).all()
    return {"runs": [_run_dict(r) for r in runs]}


@app.get("/api/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_session)):
    run = db.get(RunLog, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return _run_dict(run)


def _run_dict(r: RunLog) -> dict:
    return {
        "id": r.id, "kind": r.kind, "pipe_id": r.pipe_id, "pipe_name": r.pipe_name,
        "trigger": r.trigger, "status": r.status, "total": r.total,
        "processed": r.processed, "succeeded": r.succeeded, "failed": r.failed,
        "inserted": r.inserted, "error": r.error, "duration_ms": r.duration_ms,
        "created_at": r.created_at, "finished_at": r.finished_at,
    }


# ============ 正文补全 ============

_fulltext_running = None    # 补全线程句柄(进程内单例,与 run_log 状态双保险)


@app.post("/api/fulltext/run")
def run_fulltext(limit: int = Query(50, ge=1, le=500),
                 db: Session = Depends(get_session)):
    """手动触发一小批正文补全(限速在服务内)。已有补全在跑则拒绝。"""
    global _fulltext_running
    import threading

    running = (
        db.query(RunLog)
        .filter(RunLog.kind == "fulltext", RunLog.status == "running")
        .first()
    )
    if running:
        return {"ok": False, "error": "已有补全任务在运行"}
    if _fulltext_running is not None and _fulltext_running.is_alive():
        return {"ok": False, "error": "补全线程尚在收尾"}

    from app.services.fulltext_backfill import run_backfill

    done = threading.Event()

    def worker():
        global _fulltext_running
        try:
            run_backfill(limit=limit, sleep_seconds=1.0)
        finally:
            done.set()

    _fulltext_running = threading.Thread(target=worker, daemon=True)
    _fulltext_running.start()
    return {"ok": True, "msg": f"已开始补全 {limit} 篇,进度看运维页"}


@app.get("/api/stats")
def stats(db: Session = Depends(get_session)):
    from app.services.fulltext_backfill import FULLTEXT_MIN_WORDS

    by_kind = dict(db.query(Doc.kind, func.count(Doc.id)).group_by(Doc.kind).all())
    articles = db.query(Article).count()
    sufficient = db.query(Article).filter(Article.word_count >= FULLTEXT_MIN_WORDS).count()
    return {
        "docs": sum(by_kind.values()),
        "by_kind": by_kind,
        "articles_fulltext_sufficient": sufficient,
        "articles_fulltext_total": articles,
        "orphan_docs": _orphan_count(db),
    }


def _orphan_count(db: Session) -> int:
    from app.writer import check_orphans

    return check_orphans(db)


@app.get("/links/{doc_id}", response_class=HTMLResponse)
def doc_links(doc_id: int, db: Session = Depends(get_session)):
    """文档关系(document_link),agent 预留数据的只读出口。"""
    links = db.query(DocumentLink).filter(DocumentLink.from_doc_id == doc_id).all()
    return HTMLResponse(content=json_dump([
        {"to_doc_id": l.to_doc_id, "kind": l.kind} for l in links]))
