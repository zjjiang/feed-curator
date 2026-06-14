"""feed-curator 的 MCP server。

挂载在主应用的 /mcp 路径下（streamable-http），供本地 MCP 客户端（如小龙虾）
调用来管理订阅源。暴露四个工具：

- add_rss          ：添加 RSS 源（含 feed 可用性校验）
- search_wechat    ：搜索微信公众号，返回候选列表
- subscribe_wechat ：按 fakeid 订阅公众号并建源（自动串 we-mp-rss → feed-curator）
- list_sources     ：列出现有源（便于去重/确认）

安全说明：/mcp 与现有 JSON API 一样无鉴权，监听同一端口。本地自用场景可接受；
若部署到不可信网络，应在反向代理或此处补 token 校验，或将服务绑回 127.0.0.1。
"""

import feedparser
from mcp.server.fastmcp import FastMCP

from app.db import SessionLocal
from app.jobs.fetcher import fetch_source
from app.models import Source
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
        inserted, err = fetch_source(db, src)
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
        inserted, err = fetch_source(db, src)
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
