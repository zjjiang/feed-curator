"""建管道服务。

把"创建 Pipe 记录"的逻辑集中维护,供 API 与 MCP 工具复用。
沿用本仓库约定:时间戳是 epoch int,config 用 json_dump 序列化进 Text 列。
"""

import time

from sqlalchemy.orm import Session

from app.models import Pipe
from app.utils.json_str import json_dump

DEFAULT_WEWE_BASE_URL = "http://localhost:9001"


def _now() -> int:
    return int(time.time())


def _validate_github_config(config: dict, domain_id: int | None) -> None:
    """github 管道配置校验:共享管道必须有 query;数值字段限界。

    派生管道(query 缺省)的检索串由领域关键词实时生成,允许空 config。
    """
    if not domain_id and not str(config.get("query") or "").strip():
        raise ValueError(
            "github 共享管道需要提供 query;派生管道可省略,由领域关键词生成")
    for key in ("window_days", "min_stars", "per_page"):
        if key not in config:
            continue
        try:
            val = int(config[key])
        except (TypeError, ValueError):
            raise ValueError(f"github 配置 {key} 必须是整数")
        if key == "window_days" and val < 1:
            raise ValueError("github 配置 window_days 必须 ≥ 1")
        if key == "per_page" and not 1 <= val <= 100:
            raise ValueError("github 配置 per_page 须在 1-100 之间")
        if val < 0:
            raise ValueError(f"github 配置 {key} 不能为负")


def create_pipe(db: Session, pipe_type: str, name: str, config: dict,
                interval_min: int, domain_id: int | None = None) -> Pipe:
    if pipe_type == "github":
        _validate_github_config(config, domain_id)
    now = _now()
    pipe = Pipe(type=pipe_type, name=name, config=json_dump(config),
                domain_id=domain_id, fetch_interval_min=interval_min,
                created_at=now, updated_at=now)
    db.add(pipe)
    db.commit()
    db.refresh(pipe)
    return pipe


def create_rss_pipe(db: Session, name: str, feed_url: str,
                    interval_min: int = 30) -> Pipe:
    """RSS 共享管道。feed_url 可以是原生 feed,也可以是 RSSHub 桥接地址。"""
    return create_pipe(db, "rss", name, {"feed_url": feed_url}, interval_min)


def create_wechat_pipe(db: Session, name: str, mp_id: str, interval_min: int = 60,
                       wewe_base_url: str = DEFAULT_WEWE_BASE_URL) -> Pipe:
    """微信公众号管道。mp_id 是 we-mp-rss 库内 id(MP_WXS_xxx)。"""
    return create_pipe(db, "wechat", name,
                       {"mp_id": mp_id, "wewe_base_url": wewe_base_url},
                       interval_min)
