"""article 正文补全:限速分批,仅处理正文低于阈值的文章。

补全独立于采集(design 决策 11):一次采集触发近千请求会拖垮 fetch,
失败重试语义也不同。补全失败不阻塞、不留副作用——文档保持短正文,
kind_tag 留空待将来重判。补全记录进 run_log(kind='fulltext')供运维看板观察。
"""

import time

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Article, Doc, RunLog
from app.services import fulltext
from app.utils.html_clean import estimate_word_count

FULLTEXT_MIN_WORDS = 500
# 抓取成功但正文仍不足的短文(如博客 note),3 天内不重复抓,避免每轮空跑
RETRY_COOLDOWN_SECONDS = 3 * 86400


def pending_article_ids(db: Session, limit: int | None = None) -> list[int]:
    """正文低于阈值、且不在冷却期的文章 id,按 id 稳定排序。"""
    q = (
        db.query(Article.id)
        .join(Doc, Doc.id == Article.id)
        .filter(Doc.kind == "article")
        .filter(func.coalesce(Article.word_count, 0) < FULLTEXT_MIN_WORDS)
        .filter(Doc.last_modified_at < int(time.time()) - RETRY_COOLDOWN_SECONDS)
        .order_by(Article.id)
    )
    if limit:
        q = q.limit(limit)
    return [r[0] for r in q.all()]


def fill_article(db: Session, doc_id: int, fetch=None) -> bool:
    """抓取并回填一篇正文。失败抛 ArchiveError,调用方决定如何记录。"""
    fetch = fetch or fulltext.fetch_and_parse
    doc = db.get(Doc, doc_id)
    article = db.get(Article, doc_id)
    if doc is None or article is None:
        raise ValueError(f"article {doc_id} 不存在")

    parsed = fetch(doc.url)
    article.content_text = parsed.get("content_text") or article.content_text
    article.content_html = parsed.get("content_html") or article.content_html
    article.description = article.description or parsed.get("description") or None
    article.author = article.author or parsed.get("author") or None
    article.cover_image_url = article.cover_image_url or parsed.get("cover_image_url")
    article.word_count = estimate_word_count(article.content_text or "")
    doc.last_modified_at = int(time.time())
    db.commit()
    return True


def run_backfill(
    db: Session | None = None,
    *,
    sleep_seconds: float = 1.0,
    batch_size: int = 50,
    limit: int | None = None,
    fetch=None,
    log=print,
) -> dict:
    """跑一轮补全。限速逐篇抓取,分批把进度刷进 run_log。返回统计。"""
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        ids = pending_article_ids(db, limit=limit)
        run = RunLog(kind="fulltext", pipe_name="正文补全", trigger="manual",
                     status="running", total=len(ids), created_at=int(time.time()))
        db.add(run)
        db.commit()

        stats = {"total": len(ids), "succeeded": 0, "failed": 0}
        for i, doc_id in enumerate(ids, 1):
            try:
                fill_article(db, doc_id, fetch=fetch)
                stats["succeeded"] += 1
            except Exception as e:  # noqa: BLE001 — 单篇失败不阻塞整轮
                stats["failed"] += 1
                log(f"[fulltext] #{doc_id} 失败: {type(e).__name__}: {e}")
                db.rollback()

            if i % batch_size == 0 or i == len(ids):
                db.query(RunLog).filter(RunLog.id == run.id).update({
                    RunLog.processed: i,
                    RunLog.succeeded: stats["succeeded"],
                    RunLog.failed: stats["failed"],
                })
                db.commit()
                log(f"[fulltext] 进度 {i}/{len(ids)} 成功 {stats['succeeded']} 失败 {stats['failed']}")
            if i < len(ids):
                time.sleep(sleep_seconds)

        db.query(RunLog).filter(RunLog.id == run.id).update({
            RunLog.status: "done",
            RunLog.finished_at: int(time.time()),
        })
        db.commit()
        return stats
    finally:
        if owns_db:
            db.close()
