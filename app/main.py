import json
import os
import time
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import init_db, SessionLocal, get_session
from app.models import Source, Item, Setting
from app.jobs.fetcher import fetch_source
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
    now = int(time.time())
    db = SessionLocal()
    try:
        sources = db.query(Source).filter(Source.enabled == 1).all()
        for src in sources:
            interval = (src.fetch_interval_min or 30) * 60
            last = src.last_fetched_at or 0
            if now - last >= interval:
                count, err = fetch_source(db, src)
                if err:
                    print(f"[fetch] {src.name} 失败: {err}")
                elif count > 0:
                    print(f"[fetch] {src.name} 新增 {count} 条")
    finally:
        db.close()


def _run_scoring_cycle():
    """系统自动触发:有待处理文章且当前没有任务在跑时,自动建一个处理任务。"""
    if not _get_llm():
        return
    from app.jobs.runner import start_process_job
    job_id, created = start_process_job(trigger="auto")
    if created:
        print(f"[ai] 自动创建处理任务 #{job_id}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.add_job(_run_fetch_cycle, "interval", seconds=60, id="fetcher")
    scheduler.add_job(_run_scoring_cycle, "interval", seconds=300, id="scorer")
    scheduler.start()
    llm_status = "已配置" if _get_llm() else "未配置 DEEPSEEK_API_KEY"
    print(f"[feed-curator] 启动完成，调度器已运行，AI评分: {llm_status}")
    # 挂载式 streamable-http 必须在父应用 lifespan 内启动其 session manager，
    # 否则 /mcp 端点不工作。
    async with mcp.session_manager.run():
        print("[feed-curator] MCP server 已挂载于 /mcp")
        yield
    scheduler.shutdown()


app = FastAPI(title="feed-curator", lifespan=lifespan)
app.include_router(web_router)
# MCP（streamable-http）挂载在 /mcp。mcp_server 里把 streamable_http_path 设为 "/"，
# 经此 mount 后对外路径为 /mcp。
app.mount("/mcp", mcp.streamable_http_app())


class SourceCreate(BaseModel):
    type: str
    name: str
    config: dict
    fetch_interval_min: int = 30


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/sources")
def create_source(body: SourceCreate, db: Session = Depends(get_session)):
    now = int(time.time())
    src = Source(
        type=body.type,
        name=body.name,
        config=json.dumps(body.config, ensure_ascii=False),
        fetch_interval_min=body.fetch_interval_min,
        created_at=now,
        updated_at=now,
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return {"id": src.id, "name": src.name, "type": src.type}


@app.get("/api/sources")
def list_sources(db: Session = Depends(get_session)):
    sources = db.query(Source).order_by(Source.created_at.desc()).all()
    return [
        {
            "id": s.id,
            "type": s.type,
            "name": s.name,
            "enabled": s.enabled,
            "fetch_interval_min": s.fetch_interval_min,
            "last_fetched_at": s.last_fetched_at,
            "last_error": s.last_error,
        }
        for s in sources
    ]


@app.get("/api/items")
def list_items(
    source_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
):
    q = db.query(Item)
    if source_id:
        q = q.filter(Item.source_id == source_id)
    total = q.count()
    items = q.order_by(Item.published_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": i.id,
                "title": i.title,
                "url": i.url,
                "author": i.author,
                "source_type": i.source_type,
                "source_id": i.source_id,
                "word_count": i.word_count,
                "published_at": i.published_at,
                "fetched_at": i.fetched_at,
                "description": (i.description or "")[:200],
                "ai_score": i.ai_score,
                "ai_summary": i.ai_summary,
                "ai_keypoints": json.loads(i.ai_keypoints) if i.ai_keypoints else [],
                "ai_tags": json.loads(i.ai_tags) if i.ai_tags else [],
            }
            for i in items
        ],
    }


@app.post("/api/sources/{source_id}/fetch")
def trigger_fetch(source_id: int, db: Session = Depends(get_session)):
    src = db.query(Source).filter(Source.id == source_id).first()
    if not src:
        raise HTTPException(404, "source not found")
    count, err = fetch_source(db, src)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "inserted": count}


def _job_dict(j):
    return {
        "id": j.id,
        "status": j.status,
        "trigger": j.trigger,
        "total": j.total,
        "processed": j.processed,
        "succeeded": j.succeeded,
        "failed": j.failed,
        "error": j.error,
        "created_at": j.created_at,
        "finished_at": j.finished_at,
    }


@app.post("/api/jobs/process-all")
def process_all():
    """人工全量触发:处理所有待处理文章。已有任务在跑则复用,不新建。"""
    if not _get_llm():
        return {"ok": False, "error": "DEEPSEEK_API_KEY 未配置"}
    from app.jobs.runner import start_process_job
    job_id, created = start_process_job(trigger="manual")
    if job_id is None:
        return {"ok": True, "job_id": None, "msg": "没有待处理的文章"}
    return {"ok": True, "job_id": job_id, "created": created,
            "msg": "已创建处理任务" if created else "已有任务在运行,已复用"}


@app.get("/api/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_session)):
    from app.models import Job
    jobs = db.query(Job).order_by(Job.id.desc()).limit(limit).all()
    return {"jobs": [_job_dict(j) for j in jobs]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_session)):
    from app.models import Job
    j = db.query(Job).filter(Job.id == job_id).first()
    if not j:
        raise HTTPException(404, "job not found")
    return _job_dict(j)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job_api(job_id: int):
    from app.jobs.runner import cancel_job
    ok = cancel_job(job_id)
    return {"ok": ok, "msg": "已请求取消" if ok else "任务不在运行中"}


@app.post("/api/items/{item_id}/favorite")
def toggle_favorite(item_id: int, db: Session = Depends(get_session)):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(404, "item not found")
    item.is_favorite = 0 if item.is_favorite else 1
    db.commit()
    return {"ok": True, "is_favorite": item.is_favorite}


@app.post("/api/items/{item_id}/read")
def mark_read(item_id: int, db: Session = Depends(get_session)):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(404, "item not found")
    item.is_read = 1
    db.commit()
    return {"ok": True}


class Category(BaseModel):
    name: str
    desc: str = ""


@app.get("/api/categories")
def get_categories_api(db: Session = Depends(get_session)):
    from app.ai.scorer import get_categories
    return {"categories": get_categories(db)}


@app.post("/api/categories")
def add_category(body: Category, db: Session = Depends(get_session)):
    from app.ai.scorer import get_categories, save_categories
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "分类名不能为空")
    cats = get_categories(db)
    if any(c["name"] == name for c in cats):
        raise HTTPException(409, "分类已存在")
    cats.append({"name": name, "desc": body.desc.strip()})
    save_categories(db, cats)
    return {"ok": True, "categories": cats}


@app.delete("/api/categories/{name}")
def delete_category(name: str, db: Session = Depends(get_session)):
    from app.ai.scorer import get_categories, save_categories
    cats = [c for c in get_categories(db) if c["name"] != name]
    save_categories(db, cats)
    return {"ok": True, "categories": cats}


@app.post("/api/score/reset-failed")
def reset_failed(db: Session = Depends(get_session)):
    from app.ai.scorer import reset_failed_scores
    n = reset_failed_scores(db)
    return {"ok": True, "reset": n}


@app.post("/api/score/rescore-all")
def rescore_all_endpoint(db: Session = Depends(get_session)):
    from app.ai.scorer import rescore_all
    n = rescore_all(db)
    return {"ok": True, "cleared": n, "msg": "全部清空，下轮调度会重处理"}
