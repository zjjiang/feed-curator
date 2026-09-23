"""采集管道执行:fetch_source → upsert_doc,run_log 记录每次采集。

单一写入口在 app.writer.upsert_doc;本模块负责管道调度语义:
适配器产出 FetchedItem → 按 URL 判实体类型 → 字段分流 → 同事务入库。
单管道失败不影响其他管道(异常在函数内消化,返回错误信息)。
"""

import json
import time
from typing import Any

from sqlalchemy.orm import Session

from app.adapters import FetchedItem, get_adapter
from app.models import Domain, Pipe, RunLog
from app.services.doc_fields import build_detail, detect_doc_kind
from app.utils.html_clean import estimate_word_count
from app.writer import upsert_doc


def now_ts() -> int:
    return int(time.time())


def resolve_fetch_config(db: Session, pipe: Pipe) -> dict[str, Any]:
    """管道生效配置。派生管道的查询条件实时由所属领域关键词生成,
    领域改关键词后下次采集即用新条件。"""
    config = json.loads(pipe.config) if pipe.config else {}
    if pipe.domain_id and pipe.type == "github":
        domain = db.get(Domain, pipe.domain_id)
        keywords = json.loads(domain.keywords) if domain and domain.keywords else []
        if keywords:
            config["query"] = " ".join(keywords)[:256]
    return config


def fetch_source(db: Session, pipe: Pipe, trigger: str = "auto") -> tuple[int, str | None]:
    """抓取一个管道的全部条目并入库。返回 (新入库条数, 错误信息或 None)。

    每次抓取写一条 run_log(kind='fetch');条目级失败不中断本管道,
    汇总后记入 last_error 与 run_log.error。
    """
    started = time.monotonic()
    try:
        adapter = get_adapter(pipe.type)
        config = resolve_fetch_config(db, pipe)
        items = adapter.fetch(config)
    except Exception as e:  # noqa: BLE001 — 单管道失败必须与其他管道隔离
        error = f"{type(e).__name__}: {e}"
        _mark_pipe(db, pipe, error)
        _write_run_log(db, pipe, trigger, ok=False, inserted=0,
                       error=error, started=started)
        return 0, error

    inserted = 0
    item_errors: list[str] = []
    for fi in items:
        try:
            result = _ingest_item(db, pipe, fi)
            if result:
                inserted += 1
        except Exception as e:  # noqa: BLE001
            item_errors.append(f"{fi.external_id}: {type(e).__name__}: {e}")

    error = None
    if item_errors:
        error = f"{len(item_errors)} 条写入失败;首条: {item_errors[0]}"
        print(f"[fetch] {pipe.name} {error}")
    _mark_pipe(db, pipe, error)
    _write_run_log(db, pipe, trigger, ok=error is None, inserted=inserted,
                   error=error, started=started)
    return inserted, error


def _ingest_item(db: Session, pipe: Pipe, fi: FetchedItem) -> bool:
    """单条目入库。返回是否产生了新的采集记录。"""
    url = (fi.url or "").strip() or (fi.external_id or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"条目缺少可用 URL: {fi.title!r}")
    kind = detect_doc_kind(url)
    detail = build_detail(
        kind,
        url=url,
        author=fi.author,
        description=fi.description,
        content_text=fi.content_text,
        content_html=fi.content_html,
        cover_image_url=fi.cover_image_url,
        word_count=estimate_word_count(fi.content_text or fi.description or ""),
        published_at=fi.published_at,
        meta=fi.meta,
    )
    result = upsert_doc(
        db,
        kind=kind,
        url=url,
        title=fi.title or "(无标题)",
        detail=detail,
        pipe_id=pipe.id,
        external_id=fi.external_id,
        sort_time=fi.published_at,
    )
    return result.discovery_created


def _mark_pipe(db: Session, pipe: Pipe, error: str | None) -> None:
    pipe.last_error = error
    pipe.last_fetched_at = now_ts()
    pipe.updated_at = now_ts()
    db.commit()


def _write_run_log(db: Session, pipe: Pipe, trigger: str, ok: bool, inserted: int,
                   error: str | None, started: float) -> None:
    """写一条采集日志。失败也不应影响抓取主流程,故吞掉自身异常。"""
    try:
        db.add(RunLog(
            kind="fetch", pipe_id=pipe.id, pipe_name=pipe.name, trigger=trigger,
            status="done" if ok else "failed", inserted=inserted, error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
            created_at=now_ts(), finished_at=now_ts(),
        ))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
