import json
import time
from datetime import datetime

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Source, Item, SyncLog
from app.jobs.fetcher import fetch_source

templates = Jinja2Templates(directory="app/web/templates")
router = APIRouter()


def _fmt_time(ts: int | None) -> str:
    if not ts:
        return "-"
    dt = datetime.fromtimestamp(ts)
    now = datetime.now()
    diff = now - dt
    if diff.days == 0:
        return dt.strftime("%H:%M")
    elif diff.days < 7:
        return f"{diff.days}天前"
    return dt.strftime("%m-%d")


def _fmt_age(seconds: int | None) -> str:
    """把"距今多少秒"格式化成人类可读的相对时长。"""
    if seconds is None:
        return "从未"
    if seconds < 60:
        return f"{seconds}秒前"
    if seconds < 3600:
        return f"{seconds // 60}分钟前"
    if seconds < 86400:
        return f"{seconds // 3600}小时前"
    return f"{seconds // 86400}天前"


@router.get("/", response_class=HTMLResponse)
def items_page(
    request: Request,
    page: int = 1,
    source_id: int | None = None,
    category: str | None = None,
    sort: str = "time",
    db: Session = Depends(get_session),
):
    per_page = 50
    offset = (page - 1) * per_page

    sources = db.query(Source).order_by(Source.name).all()
    source_map = {s.id: s.name for s in sources}

    q = db.query(Item)
    if source_id:
        q = q.filter(Item.source_id == source_id)
    if category:
        # ai_tags 存的是 JSON 数组字符串,用 LIKE 粗筛(分类名带引号包裹避免子串误匹配)
        q = q.filter(Item.ai_tags.like(f'%"{category}"%'))

    total = q.count()
    if sort == "score":
        # 按星级降序;未处理(NULL)和失败(-1)排到最后,同档再按发布时间倒序。
        # 不过滤未处理文章,否则待处理居多时列表会几乎空掉。
        score_key = func.coalesce(Item.ai_score, 0)
        items_raw = (
            q.order_by(score_key.desc(), Item.published_at.desc())
            .offset(offset).limit(per_page).all()
        )
    else:
        items_raw = q.order_by(Item.published_at.desc()).offset(offset).limit(per_page).all()

    items = []
    for i in items_raw:
        preview_text = (i.content_text or i.description or "")[:300]
        meta = json.loads(i.meta) if i.meta else {}
        items.append({
            "id": i.id,
            "title": i.title,
            "url": i.url,
            "author": i.author,
            "source_type": i.source_type,
            "source_name": source_map.get(i.source_id, "?"),
            "word_count": i.word_count,
            "published_at_fmt": _fmt_time(i.published_at),
            "preview": preview_text,
            "stars": meta.get("stars"),
            "forks": meta.get("forks"),
            "language": meta.get("language"),
            "ai_score": i.ai_score,
            "ai_summary": i.ai_summary,
            "ai_keypoints": json.loads(i.ai_keypoints) if i.ai_keypoints else [],
            "ai_tags": json.loads(i.ai_tags) if i.ai_tags else [],
            "is_read": i.is_read,
            "is_favorite": i.is_favorite,
        })

    total_pages = max(1, (total + per_page - 1) // per_page)

    from app.ai.scorer import get_categories
    categories = get_categories(db)

    return templates.TemplateResponse(request, "items.html", {
        "items": items,
        "sources": [{"id": s.id, "name": s.name, "type": s.type} for s in sources],
        "source_id": source_id,
        "categories": categories,
        "category": category,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "sort": sort,
    })


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, db: Session = Depends(get_session)):
    now = int(time.time())
    day_ago = now - 86400

    sources = db.query(Source).order_by(Source.name).all()

    # 每个源的总文章数 / 近24h 新增
    total_by_src = dict(
        db.query(Item.source_id, func.count(Item.id)).group_by(Item.source_id).all()
    )
    recent_by_src = dict(
        db.query(Item.source_id, func.count(Item.id))
        .filter(Item.fetched_at >= day_ago)
        .group_by(Item.source_id)
        .all()
    )

    source_rows = []
    error_count = 0
    overdue_count = 0
    for s in sources:
        last = s.last_fetched_at or 0
        age = now - last if last else None
        interval_s = (s.fetch_interval_min or 30) * 60
        # 逾期判定：距上次抓取超过 间隔×2 视为可能卡住（只对启用源判定）
        overdue = bool(s.enabled) and last > 0 and age is not None and age > interval_s * 2
        has_error = bool(s.last_error)
        if has_error:
            error_count += 1
        if overdue:
            overdue_count += 1
        if has_error:
            health = "error"
        elif overdue or last == 0:
            health = "warn"
        else:
            health = "ok"
        source_rows.append({
            "id": s.id,
            "name": s.name,
            "type": s.type,
            "enabled": bool(s.enabled),
            "interval_min": s.fetch_interval_min,
            "total": total_by_src.get(s.id, 0),
            "recent24h": recent_by_src.get(s.id, 0),
            "last_fetched_fmt": _fmt_time(s.last_fetched_at),
            "age_str": _fmt_age(age),
            "last_error": s.last_error,
            "health": health,
        })
    # 报错和逾期的排前面
    source_rows.sort(key=lambda r: {"error": 0, "warn": 1, "ok": 2}[r["health"]])

    # AI 评分概览
    scored = db.query(Item).filter(Item.ai_score.isnot(None), Item.ai_score > 0).count()
    failed_score = db.query(Item).filter(Item.ai_score == -1).count()
    pending_score = (
        db.query(Item)
        .filter(Item.ai_score.is_(None), Item.content_text.isnot(None), Item.content_text != "")
        .count()
    )

    # 最近抓取日志
    logs = db.query(SyncLog).order_by(SyncLog.id.desc()).limit(40).all()
    log_rows = [{
        "source_name": lg.source_name,
        "source_type": lg.source_type,
        "trigger": lg.trigger,
        "ok": bool(lg.ok),
        "inserted": lg.inserted,
        "error": lg.error,
        "duration_ms": lg.duration_ms,
        "created_fmt": _fmt_time(lg.created_at),
    } for lg in logs]

    # 近24h 抓取活跃度：成功/失败次数、入库总数
    syncs_24h = db.query(SyncLog).filter(SyncLog.created_at >= day_ago).all()
    sync_ok = sum(1 for s in syncs_24h if s.ok)
    sync_fail = sum(1 for s in syncs_24h if not s.ok)
    inserted_24h = sum(s.inserted for s in syncs_24h)

    summary = {
        "source_total": len(sources),
        "source_enabled": sum(1 for s in sources if s.enabled),
        "source_error": error_count,
        "source_overdue": overdue_count,
        "item_total": sum(total_by_src.values()),
        "item_recent24h": sum(recent_by_src.values()),
        "scored": scored,
        "pending_score": pending_score,
        "failed_score": failed_score,
        "sync_ok_24h": sync_ok,
        "sync_fail_24h": sync_fail,
        "inserted_24h": inserted_24h,
    }

    return templates.TemplateResponse(request, "dashboard.html", {
        "summary": summary,
        "source_rows": source_rows,
        "log_rows": log_rows,
    })


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, db: Session = Depends(get_session)):
    sources = db.query(Source).order_by(Source.created_at.desc()).all()
    source_list = []
    for s in sources:
        source_list.append({
            "id": s.id,
            "name": s.name,
            "type": s.type,
            "fetch_interval_min": s.fetch_interval_min,
            "last_fetched_fmt": _fmt_time(s.last_fetched_at),
            "last_error": s.last_error,
        })
    return templates.TemplateResponse(request, "sources.html", {
        "sources": source_list,
    })


@router.post("/sources/add")
async def add_source(request: Request, db: Session = Depends(get_session)):
    form = await request.form()

    source_type = form.get("type", "rss")
    name = form.get("name", "")
    config_value = form.get("config_value", "")
    interval = int(form.get("interval", "30"))

    if source_type == "rss":
        config = {"feed_url": config_value}
    else:
        config = {"mp_id": config_value, "wewe_base_url": "http://localhost:9001"}

    now = int(time.time())
    src = Source(
        type=source_type,
        name=name,
        config=json.dumps(config, ensure_ascii=False),
        fetch_interval_min=interval,
        created_at=now,
        updated_at=now,
    )
    db.add(src)
    db.commit()
    return RedirectResponse("/sources", status_code=303)


@router.get("/sources/{source_id}/fetch")
def trigger_fetch_page(source_id: int, db: Session = Depends(get_session)):
    src = db.query(Source).filter(Source.id == source_id).first()
    if src:
        fetch_source(db, src, trigger="manual")
    return RedirectResponse("/sources", status_code=303)


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, db: Session = Depends(get_session)):
    from app.models import Item, Job
    pending = (
        db.query(Item)
        .filter(Item.ai_score.is_(None))
        .filter(Item.content_text.isnot(None))
        .filter(Item.content_text != "")
        .count()
    )
    jobs = db.query(Job).order_by(Job.id.desc()).limit(20).all()
    job_list = []
    for j in jobs:
        duration = None
        if j.finished_at and j.created_at:
            duration = j.finished_at - j.created_at
        job_list.append({
            "id": j.id,
            "status": j.status,
            "trigger": j.trigger,
            "total": j.total,
            "processed": j.processed,
            "succeeded": j.succeeded,
            "failed": j.failed,
            "error": j.error,
            "created_fmt": _fmt_time(j.created_at),
            "duration": duration,
            "pct": int(j.processed * 100 / j.total) if j.total else 0,
        })
    return templates.TemplateResponse(request, "jobs.html", {
        "jobs": job_list,
        "pending": pending,
    })


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_session)):
    from app.ai.scorer import get_categories
    return templates.TemplateResponse(request, "settings.html", {
        "categories": get_categories(db),
    })


@router.post("/settings/categories/add")
async def add_category_page(request: Request, db: Session = Depends(get_session)):
    from app.ai.scorer import get_categories, save_categories
    form = await request.form()
    name = (form.get("name") or "").strip()
    desc = (form.get("desc") or "").strip()
    if name:
        cats = get_categories(db)
        if not any(c["name"] == name for c in cats):
            cats.append({"name": name, "desc": desc})
            save_categories(db, cats)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/categories/delete")
async def delete_category_page(request: Request, db: Session = Depends(get_session)):
    from app.ai.scorer import get_categories, save_categories
    form = await request.form()
    name = (form.get("name") or "").strip()
    cats = [c for c in get_categories(db) if c["name"] != name]
    save_categories(db, cats)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/reset-failed")
def reset_failed_page(db: Session = Depends(get_session)):
    from app.ai.scorer import reset_failed_scores
    reset_failed_scores(db)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/rescore-all")
def rescore_all_page(db: Session = Depends(get_session)):
    from app.ai.scorer import rescore_all
    rescore_all(db)
    return RedirectResponse("/settings", status_code=303)
