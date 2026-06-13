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
    llm = _get_llm()
    if not llm:
        return
    from app.ai.scorer import run_scoring_batch
    scored = run_scoring_batch(llm, batch_size=10)
    if scored > 0:
        print(f"[ai] 本轮评分 {scored} 篇")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.add_job(_run_fetch_cycle, "interval", seconds=60, id="fetcher")
    scheduler.add_job(_run_scoring_cycle, "interval", seconds=300, id="scorer")
    scheduler.start()
    llm_status = "已配置" if _get_llm() else "未配置 DEEPSEEK_API_KEY"
    print(f"[feed-curator] 启动完成，调度器已运行，AI评分: {llm_status}")
    yield
    scheduler.shutdown()


app = FastAPI(title="feed-curator", lifespan=lifespan)
app.include_router(web_router)


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


@app.post("/api/score")
def trigger_scoring():
    llm = _get_llm()
    if not llm:
        return {"ok": False, "error": "DEEPSEEK_API_KEY 未配置"}
    from app.ai.scorer import run_scoring_batch
    scored = run_scoring_batch(llm, batch_size=10)
    return {"ok": True, "scored": scored}


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


@app.post("/api/items/{item_id}/rate")
def rate_item(item_id: int, rating: int = Query(ge=1, le=5), db: Session = Depends(get_session)):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(404, "item not found")
    item.user_rating = rating
    db.commit()
    return {"ok": True, "user_rating": rating}


class PreferencesUpdate(BaseModel):
    text: str


@app.get("/api/preferences")
def get_preferences(db: Session = Depends(get_session)):
    s = db.query(Setting).filter(Setting.key == "preferences").first()
    return {"text": s.value if s and s.value else ""}


@app.post("/api/preferences")
def set_preferences(body: PreferencesUpdate, db: Session = Depends(get_session)):
    s = db.query(Setting).filter(Setting.key == "preferences").first()
    if not s:
        s = Setting(key="preferences", value=body.text, updated_at=int(time.time()))
        db.add(s)
    else:
        s.value = body.text
        s.updated_at = int(time.time())
    db.commit()
    return {"ok": True}


@app.post("/api/score/reset-failed")
def reset_failed(db: Session = Depends(get_session)):
    from app.ai.scorer import reset_failed_scores
    n = reset_failed_scores(db)
    return {"ok": True, "reset": n}


@app.post("/api/score/rescore-all")
def rescore_all_endpoint(db: Session = Depends(get_session)):
    from app.ai.scorer import rescore_all
    n = rescore_all(db)
    return {"ok": True, "cleared": n, "msg": "全部清空，下轮调度会重评"}
