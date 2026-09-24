"""一次性迁移(本机变体):db-mp 旧库(Source/Item 扁平表)→ feed_curator_v2 新库。

与 migrate_sqlite_to_mysql.py 的差异(由本机旧库实际结构决定):
- 源是 MySQL 旧库(不是 SQLite):MIGRATION_SOURCE_URL 显式指定,目标仍是 DATABASE_URL。
- 旧库无 domains 表:跳过 domain 导入,所有管道 domain_id=NULL(领域之后在 UI 重建)。
- 旧 sources 无 domain_id 列;无 12→6 等管道合并规则(那是另一数据集的 id),1:1 迁移。
- 旧 items 无 domain_id/user_rating/note/is_dismissed/saved_at 列:
  membership 跳过,reading 只迁 is_read/is_favorite。
- 管道 config 里的 host.docker.internal 改写为 127.0.0.1(新版本在本机原生运行)。

用法(项目根目录):
    DATABASE_URL="mysql+pymysql://...@127.0.0.1:3306/feed_curator_v2?charset=utf8mb4" \
    MIGRATION_SOURCE_URL="mysql+pymysql://...@127.0.0.1:3306/feed_curator?charset=utf8mb4" \
    uv run python -m scripts.migration.migrate_legacy_mysql_to_v2

- 源库只读,不改动;目标库先清空再写入,脚本幂等可重跑。
"""

import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models import (  # noqa: E402
    Analysis, Article, Base, Discovery, Doc, Domain, DocumentLink,
    Membership, Paper, Pipe, Reading, Repo, RunLog, Suggestion,
)
from app.utils.doc_kind import detect_kind  # noqa: E402
from app.utils.json_str import json_dump  # noqa: E402
from app.utils.url_key import normalize_url  # noqa: E402

ALL_TABLES = [t.name for t in Base.metadata.sorted_tables]

# 新版本带适配器的管道类型;github 类型保留但禁用(repo 采集另立项目)
KNOWN_TYPES = {"rss", "wechat", "arxiv"}
GITHUB_DISABLED_NOTE = "repo 采集由另立项目负责;本次重构不含 github adapter,启用会报未知类型错误"

_RE_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5})(v\d+)?", re.IGNORECASE)

# meta 里这些 key 已拆入实体一等列,不再冗余进 source_meta
_META_LIFTED = {"all_authors", "categories", "pdf_url", "stars", "forks",
                "open_issues", "language", "topics", "license"}


def _require_mysql(url: str, label: str) -> str:
    url = url.strip()
    if not url.startswith("mysql"):
        sys.exit(f"拒绝执行:{label} 必须显式指向 MySQL")
    return url


def _parse_arxiv_id(url: str) -> tuple[str | None, str | None]:
    m = _RE_ARXIV.search(url or "")
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _parse_github_owner_name(url_key: str) -> tuple[str | None, str | None]:
    from urllib.parse import urlsplit
    parts = urlsplit(url_key)
    segs = [s for s in parts.path.split("/") if s]
    if parts.hostname == "github.com" and len(segs) >= 2:
        return segs[0], segs[1]
    return None, None


def _rewrite_base_urls(raw_config: str) -> str:
    """容器时代的 host.docker.internal → 本机原生可达的 127.0.0.1。"""
    if not raw_config:
        return raw_config
    cfg = json.loads(raw_config)
    return json_dump({
        k: (v.replace("host.docker.internal", "127.0.0.1") if isinstance(v, str) else v)
        for k, v in cfg.items()
    })


def _detail_for(kind: str, row, meta: dict) -> dict:
    if kind == "paper":
        arxiv_id, version = _parse_arxiv_id(row["url"])
        return {
            "abstract": row["description"],
            "content_text": row["content_text"],
            "authors": json_dump(meta.get("all_authors") or []),
            "categories": json_dump(meta.get("categories") or []),
            "pdf_url": meta.get("pdf_url"),
            "arxiv_id": arxiv_id,
            "version": version,
            "submitted_at": row["published_at"],
        }
    if kind == "repo":
        owner, name = _parse_github_owner_name(normalize_url(row["url"]))
        return {
            "owner": owner,
            "name": name,
            "description": row["description"],
            "readme_text": row["content_text"],
            "stars": meta.get("stars"),
            "forks": meta.get("forks"),
            "open_issues": meta.get("open_issues"),
            "language": meta.get("language"),
            "topics": json_dump(meta["topics"]) if meta.get("topics") else None,
            "license": meta.get("license"),
            "pushed_at": row["published_at"],
        }
    source_meta = {k: v for k, v in meta.items() if k not in _META_LIFTED}
    return {
        "author": row["author"],
        "description": row["description"],
        "content_text": row["content_text"],
        "content_html": row["content_html"],
        "cover_image_url": row["cover_image_url"],
        "word_count": row["word_count"] or 0,
        "published_at": row["published_at"],
        "source_meta": json_dump(source_meta) if source_meta else None,
    }


def _reset_target(engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        for table in ALL_TABLES:
            conn.exec_driver_sql(f"TRUNCATE TABLE {table}")
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")


def migrate() -> None:
    target_url = _require_mysql(os.environ.get("DATABASE_URL", ""), "DATABASE_URL(目标)")
    source_url = _require_mysql(os.environ.get("MIGRATION_SOURCE_URL", ""), "MIGRATION_SOURCE_URL(源)")

    src_engine = create_engine(source_url, future=True)
    target_engine = create_engine(target_url, future=True)
    _reset_target(target_engine)
    Session = sessionmaker(bind=target_engine, future=True)
    db = Session()

    counts: dict[str, int] = {}
    src = src_engine.connect()

    # ---- domain:旧库无 domains 表,保持空(领域在 UI 重建后需 rescore) ----
    counts["domain"] = 0

    # ---- sources → pipe(1:1,禁用无适配器类型,改写 base_url) ----
    for row in src.execute(text(
        "SELECT id, type, name, config, enabled, fetch_interval_min, "
        "last_fetched_at, last_error, created_at, updated_at FROM sources ORDER BY id"
    )).mappings():
        sid = row["id"]
        ptype = row["type"]
        enabled = row["enabled"]
        last_error = row["last_error"]
        if ptype == "github":
            enabled = 0
            last_error = GITHUB_DISABLED_NOTE
        elif ptype not in KNOWN_TYPES:
            enabled = 0
            last_error = f"未知管道类型 {ptype!r},已禁用"
        db.add(Pipe(
            id=sid, type=ptype, name=row["name"],
            config=_rewrite_base_urls(row["config"]),
            domain_id=None, enabled=enabled,
            fetch_interval_min=row["fetch_interval_min"],
            last_fetched_at=row["last_fetched_at"],
            last_error=last_error,
            created_at=row["created_at"], updated_at=row["updated_at"],
        ))
    db.commit()
    counts["pipe"] = db.query(Pipe).count()

    # ---- items → doc + 实体表 + discovery ----
    doc_ids_by_key: dict[str, int] = {}
    doc_times: dict[int, list[int]] = {}
    discovery_keys: set[tuple[int, str]] = set()
    dedup_rows = 0
    kind_counts = {"paper": 0, "repo": 0, "article": 0}

    result = src.execute(text(
        "SELECT id, source_id, external_id, title, url, author, description, "
        "content_text, content_html, cover_image_url, word_count, published_at, "
        "fetched_at, meta, is_read, is_favorite FROM items ORDER BY id"
    ))
    reading_flags: dict[int, tuple[int, int, int]] = {}   # doc_id → (is_read, is_favorite, fetched_at)
    for row in result.mappings():
        url_key = normalize_url(row["url"])
        kind = detect_kind(row["url"])
        dkey = (row["source_id"], row["external_id"])

        if url_key in doc_ids_by_key:
            doc_id = doc_ids_by_key[url_key]
            dedup_rows += 1
        else:
            doc = Doc(kind=kind, url_key=url_key, url=url_key, title=row["title"],
                      sort_time=row["published_at"], first_seen_at=row["fetched_at"],
                      last_modified_at=row["fetched_at"])
            db.add(doc)
            db.flush()
            doc_id = doc.id
            doc_ids_by_key[url_key] = doc_id
            meta = json.loads(row["meta"]) if row["meta"] else {}
            entity_model = {"paper": Paper, "repo": Repo, "article": Article}[kind]
            db.add(entity_model(id=doc_id, **_detail_for(kind, row, meta)))
            kind_counts[kind] += 1

        if row["is_read"] or row["is_favorite"]:
            prev = reading_flags.get(doc_id)
            cur = (row["is_read"] or 0, row["is_favorite"] or 0, row["fetched_at"])
            if prev is None or cur[2] > prev[2]:
                reading_flags[doc_id] = cur

        if dkey in discovery_keys:
            continue
        discovery_keys.add(dkey)
        db.add(Discovery(pipe_id=row["source_id"], external_id=row["external_id"],
                         doc_id=doc_id, first_seen_at=row["fetched_at"]))
        doc_times.setdefault(doc_id, []).append(row["fetched_at"])

        if len(doc_ids_by_key) % 500 == 0:
            db.commit()
    db.commit()

    for doc_id, times in doc_times.items():
        db.query(Doc).filter(Doc.id == doc_id).update({
            Doc.first_seen_at: min(times),
            Doc.last_modified_at: max(times),
        })
    db.commit()

    counts["doc"] = db.query(Doc).count()
    counts["paper"] = db.query(Paper).count()
    counts["repo"] = db.query(Repo).count()
    counts["article"] = db.query(Article).count()
    counts["discovery"] = db.query(Discovery).count()

    # ---- membership:旧库无 domain_id 列,保持空 ----
    counts["membership"] = 0

    # ---- reading(is_read / is_favorite;同 doc 取最近一次带标记的行) ----
    for doc_id, (is_read, is_favorite, seen_at) in reading_flags.items():
        db.add(Reading(doc_id=doc_id, is_read=is_read, is_favorite=is_favorite,
                       rating=None, note=None, is_dismissed=0, updated_at=seen_at))
    db.commit()
    counts["reading"] = db.query(Reading).count()

    # ---- run_log(sync_logs → kind='fetch') ----
    for row in src.execute(text(
        "SELECT source_id, source_name, `trigger`, ok, inserted, error, "
        "duration_ms, created_at FROM sync_logs"
    )).mappings():
        db.add(RunLog(
            kind="fetch", pipe_id=row["source_id"], pipe_name=row["source_name"],
            trigger=row["trigger"], status="done" if row["ok"] else "failed",
            inserted=row["inserted"], error=row["error"],
            duration_ms=row["duration_ms"], created_at=row["created_at"],
            finished_at=row["created_at"],
        ))
    db.commit()
    counts["run_log"] = db.query(RunLog).count()

    # 其余表本阶段为空,断言它们确实是空起点
    for name, model in (("analysis", Analysis), ("document_link", DocumentLink),
                        ("suggestion", Suggestion)):
        counts[name] = db.query(model).count()

    src.close()
    db.close()
    src_engine.dispose()
    target_engine.dispose()

    print("迁移完成。行数统计:")
    for k in sorted(counts):
        print(f"  {k:12s} {counts[k]}")
    print(f"  合并重复行   {dedup_rows}(url_key 归并)")
    print(f"  kind 分布    {kind_counts}")


if __name__ == "__main__":
    migrate()
