import json
import time
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters import get_adapter, FetchedItem
from app.models import Source, Item
from app.utils.html_clean import estimate_word_count


def now_ts() -> int:
    return int(time.time())


def fetch_source(db: Session, source: Source) -> tuple[int, str | None]:
    """抓取一个源的全部 item 并入库。返回 (新入库数量, 错误信息或None)。"""
    try:
        adapter = get_adapter(source.type)
        config = json.loads(source.config) if source.config else {}
        items = adapter.fetch(config)
    except Exception as e:
        source.last_error = f"{type(e).__name__}: {e}"
        source.last_fetched_at = now_ts()
        source.updated_at = now_ts()
        db.commit()
        return 0, source.last_error

    inserted = 0
    for fi in items:
        if _upsert_item(db, source, fi):
            inserted += 1

    source.last_error = None
    source.last_fetched_at = now_ts()
    source.updated_at = now_ts()
    db.commit()
    return inserted, None


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
