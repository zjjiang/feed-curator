"""手工存入 URL:manual 管道类型。

复用 fulltext 服务的解析与 SSRF 防护;article 抓正文,repo/paper 只从
URL 提取结构化字段(字段留空待补全,见 design 决策 9)。
"""

from sqlalchemy.orm import Session

from app.models import Pipe
from app.services import fulltext
from app.services.doc_fields import build_detail, detect_doc_kind
from app.services.fulltext import ArchiveError
from app.utils.html_clean import estimate_word_count
from app.writer import upsert_doc

MANUAL_PIPE_NAME = "手工存入"


def get_manual_pipe(db: Session) -> Pipe:
    """单一手工管道,懒创建。"""
    pipe = db.query(Pipe).filter(Pipe.type == "manual").first()
    if pipe is None:
        import time

        now = int(time.time())
        pipe = Pipe(type="manual", name=MANUAL_PIPE_NAME, config="{}",
                    enabled=0, created_at=now, updated_at=now)
        db.add(pipe)
        db.commit()
    return pipe


def save_url(db: Session, url: str, note: str | None = None) -> dict:
    """存入一个 URL。返回 {doc_id, kind, created, error}。

    抓取失败不阻塞入库:文档以 URL 为标题、空正文先落库,留待补全重试。
    """
    pipe = get_manual_pipe(db)
    normalized = fulltext.normalize_input_url(url)
    kind = detect_doc_kind(normalized)

    title = normalized
    detail: dict = {}
    fetch_error = None
    if kind == "article":
        try:
            parsed = fulltext.fetch_and_parse(normalized)
            title = parsed["title"] or normalized
            detail = build_detail(
                "article",
                url=parsed["url"],
                author=parsed["author"],
                description=parsed["description"] or None,
                content_text=parsed["content_text"] or None,
                content_html=parsed["content_html"] or None,
                cover_image_url=parsed["cover_image_url"],
                word_count=estimate_word_count(parsed["content_text"] or ""),
                meta={"archived_from": "manual", "note": note} if note
                else {"archived_from": "manual"},
            )
        except ArchiveError as exc:
            fetch_error = str(exc)
            detail = build_detail("article", url=normalized, meta={"archive_error": fetch_error})
    else:
        detail = build_detail(kind, url=normalized)

    result = upsert_doc(
        db,
        kind=kind,
        url=normalized,
        title=title,
        detail=detail,
        pipe_id=pipe.id,
        external_id=normalized,
    )
    return {
        "doc_id": result.doc_id,
        "kind": kind,
        "created": result.doc_created,
        "error": fetch_error,
    }
