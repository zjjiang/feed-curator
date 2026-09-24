"""feed-curator 的 MCP server。

挂载在主应用的 /mcp 路径下(streamable-http),供本地 MCP 客户端调用。
工具围绕新模型:管道管理、手工存入 URL、领域管理、推荐、任务状态。

安全说明:/mcp 与 JSON API 一样无鉴权,监听同一端口。本地自用可接受;
若部署到不可信网络,应在反向代理或此处补 token 校验,或绑回 127.0.0.1。
"""

import json
import time

import feedparser
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func

from app.ai import analyzer
from app.db import SessionLocal
from app.jobs.fetcher import fetch_source
from app.models import Analysis, Doc, Domain, Membership, Pipe, RunLog
from app.services import source_service
from app.services.wewe_client import WeweClient, WeweError

# streamable_http_path 设为 "/",配合主应用 app.mount("/mcp", ...) 得到最终 /mcp 路径
mcp = FastMCP("feed-curator", stateless_http=True, streamable_http_path="/")

_wewe = WeweClient()


@mcp.tool()
def add_rss(name: str, feed_url: str, interval_min: int = 30) -> dict:
    """添加一个 RSS 管道并立即抓取一次。

    feed_url 可以是原生 RSS/Atom 地址,也可以是 RSSHub 桥接地址。

    Args:
        name: 管道显示名称
        feed_url: RSS/Atom feed 地址
        interval_min: 自动抓取间隔(分钟),默认 30

    Returns:
        包含 pipe_id、name、inserted(首次抓取入库条数)的字典。
    """
    if not name or not name.strip():
        return {"ok": False, "error": "name 不能为空"}
    if not feed_url or not feed_url.strip():
        return {"ok": False, "error": "feed_url 不能为空"}
    if interval_min < 1:
        return {"ok": False, "error": "interval_min 必须 >= 1"}

    parsed = feedparser.parse(
        feed_url.strip(), request_headers={"User-Agent": "feed-curator/0.1 (+rss)"}
    )
    if parsed.bozo and not parsed.entries:
        reason = getattr(parsed, "bozo_exception", "无法解析为有效 feed")
        return {"ok": False, "error": f"feed 校验失败:{reason}"}
    if not parsed.entries:
        return {"ok": False, "error": "feed 可解析但没有任何条目,请确认地址正确"}

    db = SessionLocal()
    try:
        pipe = source_service.create_rss_pipe(db, name.strip(), feed_url.strip(),
                                              interval_min)
        inserted, err = fetch_source(db, pipe, trigger="manual")
        return {"ok": True, "pipe_id": pipe.id, "name": pipe.name,
                "inserted": inserted, "fetch_error": err}
    except Exception as e:  # noqa: BLE001 — 工具边界,统一转友好错误
        return {"ok": False, "error": f"建管道失败:{type(e).__name__}: {e}"}
    finally:
        db.close()


@mcp.tool()
def list_pipes() -> dict:
    """列出所有采集管道,便于去重和确认。"""
    db = SessionLocal()
    try:
        pipes = db.query(Pipe).order_by(Pipe.created_at.desc()).all()
        return {
            "ok": True,
            "count": len(pipes),
            "pipes": [
                {"id": p.id, "type": p.type, "name": p.name,
                 "enabled": bool(p.enabled), "derived_from_domain_id": p.domain_id,
                 "interval_min": p.fetch_interval_min, "last_error": p.last_error}
                for p in pipes
            ],
        }
    finally:
        db.close()


@mcp.tool()
def save_url(url: str, note: str = "") -> dict:
    """手工存入一个 URL(文章/仓库/论文)。实体类型由 URL 自动判定。

    文章抓取正文(带 SSRF 防护);仓库抓取 README;论文经 arXiv API 抓取
    摘要与作者。补全失败不阻塞入库,文档留空待重试(再次提交同一链接即重试)。

    Returns:
        {doc_id, kind, created, error, title};error 为补全失败原因(文档仍会入库)。
    """
    from app.services import manual_service

    db = SessionLocal()
    try:
        return manual_service.save_url(db, url.strip(), note=note.strip() or None)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


@mcp.tool()
def search_wechat(keyword: str, limit: int = 8) -> dict:
    """搜索微信公众号,返回候选列表(含 fakeid)。

    选定后用 subscribe_wechat 传对应的 fakeid 订阅。
    """
    try:
        candidates = _wewe.search(keyword, limit=limit)
        return {"ok": True, "count": len(candidates),
                "candidates": [c.to_dict() for c in candidates]}
    except WeweError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def subscribe_wechat(mp_name: str, fakeid: str, interval_min: int = 60) -> dict:
    """订阅一个微信公众号并建管道,自动完成全链路。"""
    if not mp_name or not fakeid:
        return {"ok": False, "error": "mp_name 与 fakeid 不能为空(fakeid 用 search_wechat 获取)"}
    try:
        mp_id = _wewe.subscribe(mp_name.strip(), fakeid.strip())
    except WeweError as e:
        return {"ok": False, "error": str(e)}
    _wewe.trigger_update(mp_id)

    db = SessionLocal()
    try:
        pipe = source_service.create_wechat_pipe(
            db, mp_name.strip(), mp_id, interval_min, wewe_base_url=_wewe.base_url)
        inserted, err = fetch_source(db, pipe, trigger="manual")
        return {"ok": True, "pipe_id": pipe.id, "mp_id": mp_id,
                "inserted": inserted, "fetch_error": err,
                "note": "若 inserted 为 0,是上游还在抓文章,稍后会被定时任务补上"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"建管道失败:{type(e).__name__}: {e}"}
    finally:
        db.close()


# ============ 领域 ============


@mcp.tool()
def list_domains() -> dict:
    """列出所有领域(名称、描述、关键词、启用状态)。"""
    db = SessionLocal()
    try:
        domains = db.query(Domain).order_by(Domain.id).all()
        return {"ok": True, "count": len(domains), "domains": [
            {"id": d.id, "name": d.name, "description": d.description,
             "keywords": json.loads(d.keywords) if d.keywords else [],
             "enabled": bool(d.enabled)} for d in domains
        ]}
    finally:
        db.close()


@mcp.tool()
def create_domain(name: str, description: str = "", keywords: str = "") -> dict:
    """新建领域。领域 = AI 判定的归属单位;关键词喂给 LLM 辅助判定,
    并实时生成为派生管道的查询条件。

    Args:
        name: 领域名(唯一)
        description: 描述,说明这个领域追踪什么
        keywords: 逗号分隔的关键词列表
    """
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "name 不能为空"}
    from app.utils.json_str import json_dump

    db = SessionLocal()
    try:
        if db.query(Domain).filter(Domain.name == name).first():
            return {"ok": False, "error": f"领域「{name}」已存在"}
        kws = [k.strip() for k in keywords.replace("，", ",").split(",") if k.strip()]
        now = int(time.time())
        d = Domain(name=name, description=description.strip(),
                   keywords=json_dump(kws), enabled=1, created_at=now, updated_at=now)
        db.add(d)
        db.commit()
        return {"ok": True, "domain_id": d.id, "keywords": kws}
    finally:
        db.close()


@mcp.tool()
def update_domain_keywords(name: str, keywords: str) -> dict:
    """整体替换某领域的关键词(逗号分隔)。派生管道下次采集即用新条件。"""
    name = (name or "").strip()
    from app.utils.json_str import json_dump

    db = SessionLocal()
    try:
        d = db.query(Domain).filter(Domain.name == name).first()
        if not d:
            return {"ok": False, "error": f"领域「{name}」不存在"}
        kws = [k.strip() for k in keywords.replace("，", ",").split(",") if k.strip()]
        d.keywords = json_dump(kws)
        d.updated_at = int(time.time())
        db.commit()
        return {"ok": True, "keywords": kws}
    finally:
        db.close()


# ============ 推荐 / 状态 ============


@mcp.tool()
def recommend_articles(days: int = 7, limit: int = 5, min_stars: int = 4,
                       domain: str | None = None) -> dict:
    """推荐最近一段时间 AI 判定星级最高的文档。

    Args:
        days: 时间窗口(按 doc.sort_time),默认 7 天
        limit: 篇数上限,默认 5,最多 50
        min_stars: 最低星级门槛(1-5),默认 4
        domain: 可选,限定领域名(精确匹配)
    """
    if days < 1:
        return {"ok": False, "error": "days 必须 >= 1"}
    if not 1 <= min_stars <= 5:
        return {"ok": False, "error": "min_stars 必须在 1-5 之间"}
    limit = max(1, min(limit, 50))
    since = int(time.time()) - days * 86400

    db = SessionLocal()
    try:
        latest = (
            db.query(Analysis.doc_id, func.max(Analysis.id).label("aid"))
            .filter(Analysis.status == "ok")
            .group_by(Analysis.doc_id)
            .subquery()
        )
        q = (
            db.query(Doc, Analysis)
            .join(latest, latest.c.doc_id == Doc.id)
            .join(Analysis, Analysis.id == latest.c.aid)
            .filter(Analysis.stars >= min_stars)
            .filter(Doc.sort_time >= since)
        )
        if domain:
            q = q.join(Membership, Membership.doc_id == Doc.id) \
                .join(Domain, Domain.id == Membership.domain_id) \
                .filter(Domain.name == domain)
        rows = q.order_by(Analysis.stars.desc(), Doc.sort_time.desc()) \
            .limit(limit).all()

        articles = [{
            "doc_id": d.id, "kind": d.kind, "title": d.title, "url": d.url,
            "stars": a.stars, "summary": a.summary or "",
            "keypoints": json.loads(a.keypoints) if a.keypoints else [],
            "domains": json.loads(a.domains) if a.domains else [],
            "article_kind": a.article_kind,
            "sort_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(d.sort_time)),
        } for d, a in rows]

        result = {"ok": True, "window": f"最近 {days} 天", "min_stars": min_stars,
                  "domain": domain, "count": len(articles), "articles": articles}
        if not articles:
            result["note"] = "窗口内没有达标文档;可能尚未跑判定,或可降低 min_stars / 扩大 days。"
        return result
    finally:
        db.close()


@mcp.tool()
def job_status() -> dict:
    """查询判定任务状态:正在跑的任务或最近一个,以及待判定文档数。"""
    db = SessionLocal()
    try:
        pending = len(analyzer.select_doc_ids(db))
        run = (
            db.query(RunLog).filter(RunLog.kind == "analyze", RunLog.status == "running")
            .order_by(RunLog.id.desc()).first()
        )
        if run is None:
            run = db.query(RunLog).filter(RunLog.kind == "analyze") \
                .order_by(RunLog.id.desc()).first()
        if run is None:
            return {"ok": True, "run": None, "pending": pending,
                    "note": "还没有任何判定任务记录"}
        pct = int(run.processed * 100 / run.total) if run.total else 0
        return {
            "ok": True, "pending": pending,
            "run": {
                "id": run.id, "status": run.status, "trigger": run.trigger,
                "total": run.total, "processed": run.processed,
                "succeeded": run.succeeded, "failed": run.failed,
                "percent": pct, "error": run.error,
                "created_at": _fmt_epoch(run.created_at),
                "finished_at": _fmt_epoch(run.finished_at),
            },
        }
    finally:
        db.close()


def _fmt_epoch(ts: int | None) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
