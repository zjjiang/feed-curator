import json
import time
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters import get_adapter, FetchedItem
from app.models import Source, Item, SyncLog
from app.utils.html_clean import estimate_word_count


def now_ts() -> int:
    return int(time.time())


def fetch_source(db: Session, source: Source, trigger: str = "auto") -> tuple[int, str | None]:
    """抓取一个源的全部 item 并入库。返回 (新入库数量, 错误信息或None)。
    每次抓取都会写一条 SyncLog，供运维看板观察同步历史。"""
    started = time.monotonic()
    try:
        adapter = get_adapter(source.type)
        config = json.loads(source.config) if source.config else {}
        items = adapter.fetch(config)
    except Exception as e:
        source.last_error = f"{type(e).__name__}: {e}"
        source.last_fetched_at = now_ts()
        source.updated_at = now_ts()
        db.commit()
        _write_sync_log(db, source, trigger, ok=False, inserted=0,
                        error=source.last_error, started=started)
        return 0, source.last_error

    inserted = 0
    for fi in items:
        if _upsert_item(db, source, fi):
            inserted += 1

    source.last_error = None
    source.last_fetched_at = now_ts()
    source.updated_at = now_ts()
    db.commit()
    _write_sync_log(db, source, trigger, ok=True, inserted=inserted,
                    error=None, started=started)
    return inserted, None


def _write_sync_log(
    db: Session,
    source: Source,
    trigger: str,
    ok: bool,
    inserted: int,
    error: str | None,
    started: float,
) -> None:
    """写一条抓取日志。失败也不应影响抓取主流程，故吞掉自身异常。"""
    try:
        log = SyncLog(
            source_id=source.id,
            source_name=source.name,
            source_type=source.type,
            trigger=trigger,
            ok=1 if ok else 0,
            inserted=inserted,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
            created_at=now_ts(),
        )
        db.add(log)
        db.commit()
    except Exception:
        db.rollback()


def _upsert_item(db: Session, source: Source, fi: FetchedItem) -> bool:
    """如果不存在就插入，存在就跳过。返回是否新插入。"""
    existing = (
        db.query(Item)
        .filter(Item.source_id == source.id, Item.external_id == fi.external_id)
        .first()
    )
    if existing:
        return False

    word_count = estimate_word_count(fi.content_text or fi.description or "")
    item = Item(
        source_id=source.id,
        source_type=source.type,
        external_id=fi.external_id,
        title=fi.title,
        url=fi.url,
        author=fi.author,
        description=fi.description,
        content_text=fi.content_text,
        content_html=fi.content_html,
        cover_image_url=fi.cover_image_url,
        word_count=word_count,
        published_at=fi.published_at,
        fetched_at=now_ts(),
        meta=json.dumps(fi.meta, ensure_ascii=False) if fi.meta else None,
    )
    db.add(item)
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
