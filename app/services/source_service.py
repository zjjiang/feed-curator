"""建源服务。

把"创建 Source 记录"的逻辑从 main.py / web/pages.py 抽出来集中维护，供 MCP 工具
复用。遵循本仓库约定：时间戳是 epoch int，config 用 json.dumps(ensure_ascii=False)
序列化进 Text 列。
"""

import json
import time

from sqlalchemy.orm import Session

from app.models import Source

DEFAULT_WEWE_BASE_URL = "http://localhost:9001"


def _now() -> int:
    return int(time.time())


def create_source(db: Session, source_type: str, name: str, config: dict, interval_min: int) -> Source:
    """创建并持久化一个 Source，返回新建对象。"""
    now = _now()
    src = Source(
        type=source_type,
        name=name,
        config=json.dumps(config, ensure_ascii=False),
        fetch_interval_min=interval_min,
        created_at=now,
        updated_at=now,
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


def create_rss_source(db: Session, name: str, feed_url: str, interval_min: int = 30) -> Source:
    """创建一个 RSS 源。feed_url 可以是原生 feed，也可以是 RSSHub 桥接地址。"""
    return create_source(db, "rss", name, {"feed_url": feed_url}, interval_min)


def create_wechat_source(
    db: Session,
    name: str,
    mp_id: str,
    interval_min: int = 60,
    wewe_base_url: str = DEFAULT_WEWE_BASE_URL,
) -> Source:
    """创建一个微信公众号源。mp_id 是 we-mp-rss 库内 id（MP_WXS_xxx）。"""
    config = {"mp_id": mp_id, "wewe_base_url": wewe_base_url}
    return create_source(db, "wechat", name, config, interval_min)
