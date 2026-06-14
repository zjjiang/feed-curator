"""feed-curator 的 MCP server。

挂载在主应用的 /mcp 路径下（streamable-http），供本地 MCP 客户端（如小龙虾）
调用来管理订阅源。暴露四个工具：

- add_rss          ：添加 RSS 源（含 feed 可用性校验）
- search_wechat    ：搜索微信公众号，返回候选列表
- subscribe_wechat ：按 fakeid 订阅公众号并建源（自动串 we-mp-rss → feed-curator）
- list_sources     ：列出现有源（便于去重/确认）
- recommend_articles：按时间窗口推荐评分最高的几篇文章
- list_categories / add_category / update_category / delete_category：分类关键词增删改查
- rescore_all_articles：清空所有评分并重新跑全量分类
- job_status        ：查询当前/最近的处理任务状态

安全说明：/mcp 与现有 JSON API 一样无鉴权，监听同一端口。本地自用场景可接受；
若部署到不可信网络，应在反向代理或此处补 token 校验，或将服务绑回 127.0.0.1。
"""

import json
import time

import feedparser
from mcp.server.fastmcp import FastMCP

from app.db import SessionLocal
from app.jobs.fetcher import fetch_source
from app.models import Item, Job, Source
from app.services import source_service
from app.services.wewe_client import WeweClient, WeweError

# streamable_http_path 设为 "/"，配合主应用 app.mount("/mcp", ...) 得到最终 /mcp 路径
mcp = FastMCP("feed-curator", stateless_http=True, streamable_http_path="/")

# WeweClient 内部只缓存 token，复用一个实例即可
_wewe = WeweClient()


@mcp.tool()
def add_rss(name: str, feed_url: str, interval_min: int = 30) -> dict:
    """添加一个 RSS 订阅源并立即抓取一次。

    feed_url 可以是原生 RSS/Atom 地址，也可以是 RSSHub 桥接地址
    （如 http://localhost:9002/36kr/news/latest）。

    Args:
        name: 源的显示名称
        feed_url: RSS/Atom feed 地址
        interval_min: 自动抓取间隔（分钟），默认 30

    Returns:
        包含 source_id、name、inserted（首次抓取入库条数）的字典。
    """
    if not name or not name.strip():
        return {"ok": False, "error": "name 不能为空"}
    if not feed_url or not feed_url.strip():
        return {"ok": False, "error": "feed_url 不能为空"}
    if interval_min < 1:
        return {"ok": False, "error": "interval_min 必须 >= 1"}

    # fail fast：先校验 feed 能解析且有内容，避免建一个永远抓不到东西的源
    parsed = feedparser.parse(
        feed_url.strip(), request_headers={"User-Agent": "feed-curator/0.1 (+rss)"}
    )
    if parsed.bozo and not parsed.entries:
        reason = getattr(parsed, "bozo_exception", "无法解析为有效 feed")
        return {"ok": False, "error": f"feed 校验失败：{reason}"}
    if not parsed.entries:
        return {"ok": False, "error": "feed 可解析但没有任何条目，请确认地址正确"}

    db = SessionLocal()
    try:
        src = source_service.create_rss_source(db, name.strip(), feed_url.strip(), interval_min)
        inserted, err = fetch_source(db, src, trigger="manual")
        return {
            "ok": True,
            "source_id": src.id,
            "name": src.name,
            "inserted": inserted,
            "fetch_error": err,
        }
    except Exception as e:  # noqa: BLE001 — 工具边界，统一转友好错误
        return {"ok": False, "error": f"建源失败：{type(e).__name__}: {e}"}
    finally:
        db.close()


@mcp.tool()
def search_wechat(keyword: str, limit: int = 8) -> dict:
    """搜索微信公众号，返回候选列表（含 fakeid）。

    同名公众号很多，结果里 nickname/alias/signature 用于辨认是不是目标官方号。
    选定后用 subscribe_wechat 传对应的 fakeid 订阅。

    Args:
        keyword: 公众号名称关键词
        limit: 返回候选数量上限，默认 8

    Returns:
        包含 candidates 列表的字典，每项有 fakeid/nickname/alias/signature。
    """
    try:
        candidates = _wewe.search(keyword, limit=limit)
        return {
            "ok": True,
            "count": len(candidates),
            "candidates": [c.to_dict() for c in candidates],
        }
    except WeweError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def subscribe_wechat(mp_name: str, fakeid: str, interval_min: int = 60) -> dict:
    """订阅一个微信公众号并在 feed-curator 建源，自动完成全链路。

    流程：we-mp-rss 订阅 → 拿库内 id → 建 feed-curator 源 → 触发上游抓取 → 抓取入库。
    fakeid 来自 search_wechat 的结果，确保订阅到正确的公众号。

    Args:
        mp_name: 公众号名称（建源时的显示名）
        fakeid: 来自 search_wechat 的 fakeid
        interval_min: 自动抓取间隔（分钟），默认 60

    Returns:
        包含 source_id、mp_id、inserted 的字典。
    """
    if not mp_name or not mp_name.strip():
        return {"ok": False, "error": "mp_name 不能为空"}
    if not fakeid or not fakeid.strip():
        return {"ok": False, "error": "fakeid 不能为空（请先用 search_wechat 获取）"}
    if interval_min < 1:
        return {"ok": False, "error": "interval_min 必须 >= 1"}

    # 1. we-mp-rss 订阅，拿库内 id
    try:
        mp_id = _wewe.subscribe(mp_name.strip(), fakeid.strip())
    except WeweError as e:
        return {"ok": False, "error": str(e)}

    # 2. 触发上游抓文章（尽力而为，限流/失败不阻断）
    _wewe.trigger_update(mp_id)

    # 3. 建 feed-curator 源并抓取入库
    db = SessionLocal()
    try:
        src = source_service.create_wechat_source(
            db, mp_name.strip(), mp_id, interval_min, wewe_base_url=_wewe.base_url
        )
        inserted, err = fetch_source(db, src, trigger="manual")
        return {
            "ok": True,
            "source_id": src.id,
            "mp_id": mp_id,
            "name": src.name,
            "inserted": inserted,
            "fetch_error": err,
            "note": "若 inserted 为 0，是上游 we-mp-rss 还在抓文章，稍后会被定时任务补上",
        }
    except Exception as e:  # noqa: BLE001 — 工具边界
        return {"ok": False, "error": f"建源失败：{type(e).__name__}: {e}"}
    finally:
        db.close()


@mcp.tool()
def list_sources() -> dict:
    """列出 feed-curator 当前所有订阅源，便于去重和确认。

    Returns:
        包含 sources 列表的字典，每项有 id/type/name/enabled/interval。
    """
    db = SessionLocal()
    try:
        sources = db.query(Source).order_by(Source.created_at.desc()).all()
        return {
            "ok": True,
            "count": len(sources),
            "sources": [
                {
                    "id": s.id,
                    "type": s.type,
                    "name": s.name,
                    "enabled": bool(s.enabled),
                    "interval_min": s.fetch_interval_min,
                    "last_error": s.last_error,
                }
                for s in sources
            ],
        }
    finally:
        db.close()


@mcp.tool()
def recommend_articles(
    days: int = 7,
    limit: int = 5,
    min_score: int = 4,
    category: str | None = None,
) -> dict:
    """推荐最近一段时间内 AI 评分最高的几篇文章。

    按文章发布时间筛选时间窗口，只在已评分（ai_score 1-5）的文章里挑，
    按星级降序、同星级取较新的。适合"给我看看这周最值得读的几篇"这类请求。

    Args:
        days: 时间窗口，往前回溯的天数（按发布时间），默认 7。
        limit: 返回篇数上限，默认 5，最多 50。
        min_score: 最低星级门槛（1-5），默认 4（即只推 4-5 星）。
        category: 可选，限定某个分类名（精确匹配分类标签）。

    Returns:
        包含 articles 列表的字典，每项有 title/url/score/summary/keypoints/
        tags/source/published_at。另含 window（时间窗口描述）和 count。
        若窗口内没有达标文章，articles 为空并在 note 里说明。
    """
    if days < 1:
        return {"ok": False, "error": "days 必须 >= 1"}
    if not (1 <= min_score <= 5):
        return {"ok": False, "error": "min_score 必须在 1-5 之间"}
    limit = max(1, min(limit, 50))

    now = int(time.time())
    since = now - days * 86400

    db = SessionLocal()
    try:
        source_map = {s.id: s.name for s in db.query(Source).all()}

        q = (
            db.query(Item)
            .filter(Item.ai_score >= min_score)  # >= min_score 隐含排除 NULL 和 -1
            .filter(Item.published_at.isnot(None))
            .filter(Item.published_at >= since)
        )
        if category:
            # ai_tags 是 JSON 数组字符串，带引号包裹避免子串误匹配
            q = q.filter(Item.ai_tags.like(f'%"{category}"%'))

        rows = (
            q.order_by(Item.ai_score.desc(), Item.published_at.desc())
            .limit(limit)
            .all()
        )

        articles = []
        for i in rows:
            articles.append({
                "title": i.title,
                "url": i.url,
                "score": i.ai_score,
                "summary": i.ai_summary or "",
                "keypoints": json.loads(i.ai_keypoints) if i.ai_keypoints else [],
                "tags": json.loads(i.ai_tags) if i.ai_tags else [],
                "source": source_map.get(i.source_id, "?"),
                "published_at": _fmt_epoch(i.published_at),
            })

        result = {
            "ok": True,
            "window": f"最近 {days} 天（发布时间）",
            "min_score": min_score,
            "category": category,
            "count": len(articles),
            "articles": articles,
        }
        if not articles:
            # 区分两种空结果：是没达标，还是这段时间压根没评分文章
            scored_in_window = (
                db.query(Item)
                .filter(Item.ai_score > 0)
                .filter(Item.published_at >= since)
                .count()
            )
            if scored_in_window == 0:
                result["note"] = (
                    f"最近 {days} 天内还没有已评分的文章。可能是文章未处理，"
                    f"先在 feed-curator 触发 AI 处理，或扩大 days。"
                )
            else:
                result["note"] = (
                    f"最近 {days} 天有 {scored_in_window} 篇已评分文章，"
                    f"但没有达到 {min_score} 星的。可降低 min_score 再试。"
                )
        return result
    finally:
        db.close()


def _fmt_epoch(ts: int | None) -> str:
    """epoch 秒 → 'YYYY-MM-DD HH:MM' 本地时间字符串。"""
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


# ============ 分类关键词增删改查 ============
# 分类表是 AI 给文章打标签时的候选集，存在 Setting 表的 categories 记录里。
# 每项形如 {"name": "AI", "desc": "模型进展与应用"}，desc 会喂给 LLM 辅助打标。


@mcp.tool()
def list_categories() -> dict:
    """列出当前所有分类关键词（AI 给文章打标签时的候选集）。

    Returns:
        包含 categories 列表的字典，每项有 name/desc。
    """
    from app.ai.scorer import get_categories

    db = SessionLocal()
    try:
        cats = get_categories(db)
        return {"ok": True, "count": len(cats), "categories": cats}
    finally:
        db.close()


@mcp.tool()
def add_category(name: str, desc: str = "") -> dict:
    """新增一个分类关键词。

    Args:
        name: 分类名（唯一，已存在则报错）。
        desc: 分类说明，会喂给 LLM 帮助判断文章是否属于该类，建议填写。

    Returns:
        操作结果 + 更新后的完整分类列表。
    """
    from app.ai.scorer import get_categories, save_categories

    if not name or not name.strip():
        return {"ok": False, "error": "name 不能为空"}
    name = name.strip()

    db = SessionLocal()
    try:
        cats = get_categories(db)
        if any(c["name"] == name for c in cats):
            return {"ok": False, "error": f"分类「{name}」已存在，如需改说明请用 update_category"}
        cats.append({"name": name, "desc": desc.strip()})
        save_categories(db, cats)
        return {"ok": True, "message": f"已新增分类「{name}」", "categories": cats}
    finally:
        db.close()


@mcp.tool()
def update_category(name: str, desc: str) -> dict:
    """修改一个已有分类的说明（desc）。分类名本身不可改，要改名请删了重建。

    Args:
        name: 要修改的分类名（须已存在）。
        desc: 新的分类说明。

    Returns:
        操作结果 + 更新后的完整分类列表。
    """
    from app.ai.scorer import get_categories, save_categories

    if not name or not name.strip():
        return {"ok": False, "error": "name 不能为空"}
    name = name.strip()

    db = SessionLocal()
    try:
        cats = get_categories(db)
        hit = next((c for c in cats if c["name"] == name), None)
        if hit is None:
            return {"ok": False, "error": f"分类「{name}」不存在"}
        hit["desc"] = desc.strip()
        save_categories(db, cats)
        return {"ok": True, "message": f"已更新分类「{name}」的说明", "categories": cats}
    finally:
        db.close()


@mcp.tool()
def delete_category(name: str) -> dict:
    """删除一个分类关键词。

    注意：删除分类不会改动已打在文章上的旧标签；要让分类调整全面生效，
    可随后调用 rescore_all_articles 重新跑全量分类。

    Args:
        name: 要删除的分类名。

    Returns:
        操作结果 + 删除后的完整分类列表。
    """
    from app.ai.scorer import get_categories, save_categories

    if not name or not name.strip():
        return {"ok": False, "error": "name 不能为空"}
    name = name.strip()

    db = SessionLocal()
    try:
        cats = get_categories(db)
        new_cats = [c for c in cats if c["name"] != name]
        if len(new_cats) == len(cats):
            return {"ok": False, "error": f"分类「{name}」不存在"}
        save_categories(db, new_cats)
        return {"ok": True, "message": f"已删除分类「{name}」", "categories": new_cats}
    finally:
        db.close()


# ============ 全量重跑 ============


@mcp.tool()
def rescore_all_articles() -> dict:
    """清空所有文章的 AI 评分结果，并立即启动一个任务按当前分类表重新处理全部文章。

    适用场景：调整了分类关键词后，想让改动对历史文章也生效。
    这会重新调用 LLM 处理每一篇有正文的文章，耗时和 token 消耗都不小。
    用 job_status 查看进度。

    Returns:
        清空篇数、新任务 id、待处理篇数。若未配置 DEEPSEEK_API_KEY，
        评分会清空但任务无法启动（started=False）。
    """
    from app.ai.scorer import rescore_all
    from app.jobs.runner import start_process_job

    db = SessionLocal()
    try:
        cleared = rescore_all(db)
    finally:
        db.close()

    job_id, created = start_process_job(trigger="manual")
    if job_id is None:
        return {
            "ok": True,
            "cleared": cleared,
            "started": False,
            "note": "已清空评分，但没有可处理的文章（无正文或未配置 API key）",
        }
    return {
        "ok": True,
        "cleared": cleared,
        "started": True,
        "job_id": job_id,
        "is_new_job": created,
        "note": "已开始重新处理，用 job_status 查看进度",
    }


# ============ 任务状态 ============


@mcp.tool()
def job_status(job_id: int | None = None) -> dict:
    """查询 AI 处理任务的状态与进度。

    Args:
        job_id: 可选。指定则查该任务；不指定则返回当前正在跑的任务，
                若没有在跑的则返回最近一个任务。

    Returns:
        任务的 status（running/done/failed/cancelled）、trigger、
        total/processed/succeeded/failed 计数、进度百分比、时间。
        另含 pending（当前还有多少篇待处理）。
    """
    db = SessionLocal()
    try:
        if job_id is not None:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job is None:
                return {"ok": False, "error": f"任务 {job_id} 不存在"}
        else:
            job = (
                db.query(Job)
                .filter(Job.status == "running")
                .order_by(Job.id.desc())
                .first()
            )
            if job is None:
                job = db.query(Job).order_by(Job.id.desc()).first()

        pending = (
            db.query(Item.id)
            .filter(Item.ai_score.is_(None))
            .filter(Item.content_text.isnot(None))
            .filter(Item.content_text != "")
            .count()
        )

        if job is None:
            return {
                "ok": True,
                "job": None,
                "pending": pending,
                "note": "还没有任何处理任务记录",
            }

        pct = int(job.processed * 100 / job.total) if job.total else 0
        duration = (
            (job.finished_at - job.created_at)
            if job.finished_at and job.created_at
            else None
        )
        return {
            "ok": True,
            "pending": pending,
            "job": {
                "id": job.id,
                "status": job.status,
                "trigger": job.trigger,
                "total": job.total,
                "processed": job.processed,
                "succeeded": job.succeeded,
                "failed": job.failed,
                "percent": pct,
                "created_at": _fmt_epoch(job.created_at),
                "finished_at": _fmt_epoch(job.finished_at),
                "duration_sec": duration,
                "error": job.error,
            },
        }
    finally:
        db.close()
