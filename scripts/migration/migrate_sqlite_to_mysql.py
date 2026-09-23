"""一次性迁移:SQLite 源库(只读) → MySQL 目标库。

用法(项目根目录):
    DATABASE_URL="mysql+pymysql://..." uv run python -m scripts.migration.migrate_sqlite_to_mysql

- 源库只读,不改动;目标库先清空再写入,脚本幂等可重跑。
- 管道合并:12→6, 15→7, 16→10;幸存管道沿用旧 id。
- 迁移完成后用 scripts.migration.verify_migration 核对全部断言。
"""

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models import (  # noqa: E402
    Analysis, Article, Base, Discovery, Doc, Domain, DocumentLink,
    Membership, Paper, Pipe, Reading, Repo, RunLog, Suggestion,
)
from app.utils.doc_kind import detect_kind  # noqa: E402
from app.utils.json_str import json_dump  # noqa: E402
from app.utils.url_key import normalize_url  # noqa: E402

SOURCE_DB = PROJECT_ROOT / "data" / "feed-curator.db"

ALL_TABLES = [t.name for t in Base.metadata.sorted_tables]

# 合并规则(design.md 决策/迁移计划 step 3)
PIPE_MERGE = {12: 6, 15: 7, 16: 10}
GITHUB_DISABLED_NOTE = "repo 采集由另立项目负责;本次重构不含 github adapter,启用会报未知类型错误"

_RE_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5})(v\d+)?", re.IGNORECASE)

# meta 里这些 key 已拆入实体一等列,不再冗余进 source_meta
_META_LIFTED = {"all_authors", "categories", "pdf_url", "stars", "forks",
                "open_issues", "language", "topics", "license"}


def _parse_arxiv_id(url: str) -> tuple[str | None, str | None]:
    m = _RE_ARXIV.search(url or "")
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _parse_github_owner_name(url_key: str) -> tuple[str | None, str | None]:
    parts = urlsplit(url_key)
    segs = [s for s in parts.path.split("/") if s]
    if parts.hostname == "github.com" and len(segs) >= 2:
        return segs[0], segs[1]
    return None, None


def _pipe_config(source_id: int, raw_config: str) -> str:
    cfg = json.loads(raw_config) if raw_config else {}
    if source_id == 11:
        # 转共享管道:去掉关键词过滤(cs.RO 未被 6 覆盖)
        return json_dump({"category": cfg.get("category", "cs.RO"),
                          "max_results": cfg.get("max_results", 50)})
    return json_dump(cfg)


def _detail_for(kind: str, row: sqlite3.Row, meta: dict) -> dict:
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
            "pushed_at": row["published_at"],   # 旧 github adapter 存的就是 pushed_at
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
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url.startswith("mysql"):
        sys.exit("拒绝执行:必须显式设置 DATABASE_URL 指向 MySQL 目标库(保护 SQLite 回退路径)")

    from sqlalchemy import create_engine

    src = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    target_engine = create_engine(database_url, future=True)
    _reset_target(target_engine)
    Session = sessionmaker(bind=target_engine, future=True)
    db = Session()

    counts: dict[str, int] = {}

    # ---- domain ----
    for row in src.execute("SELECT * FROM domains"):
        db.add(Domain(id=row["id"], name=row["name"], description=row["description"],
                      keywords=row["keywords"], enabled=row["enabled"],
                      created_at=row["created_at"], updated_at=row["updated_at"]))
    db.commit()
    counts["domain"] = db.query(Domain).count()

    # ---- pipe(16 行 → 13 行) ----
    for row in src.execute("SELECT * FROM sources ORDER BY id"):
        sid = row["id"]
        if sid in PIPE_MERGE:
            continue
        db.add(Pipe(
            id=sid,
            type=row["type"],
            name="arXiv cs.RO" if sid == 11 else row["name"],
            config=_pipe_config(sid, row["config"]),
            domain_id=None if sid == 11 else row["domain_id"],
            enabled=0 if sid in (13, 14) else row["enabled"],
            fetch_interval_min=row["fetch_interval_min"],
            last_fetched_at=row["last_fetched_at"],
            last_error=GITHUB_DISABLED_NOTE if sid in (13, 14) else row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        ))
    db.commit()
    counts["pipe"] = db.query(Pipe).count()

    # ---- items → doc + 实体表 + discovery ----
    doc_ids_by_key: dict[str, int] = {}          # url_key → doc.id
    doc_times: dict[int, list[int]] = {}         # doc.id → [fetched_at...]
    discovery_keys: set[tuple[int, str]] = set()
    dedup_rows = 0
    kind_counts = {"paper": 0, "repo": 0, "article": 0}

    for row in src.execute("SELECT * FROM items ORDER BY id"):
        url_key = normalize_url(row["url"])
        kind = detect_kind(row["url"])
        pipe_id = PIPE_MERGE.get(row["source_id"], row["source_id"])
        dkey = (pipe_id, row["external_id"])

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

        if dkey in discovery_keys:
            continue    # 合并管道后同 (pipe_id, external_id) 的真实重复,被唯一约束吸收
        discovery_keys.add(dkey)
        db.add(Discovery(pipe_id=pipe_id, external_id=row["external_id"],
                         doc_id=doc_id, first_seen_at=row["fetched_at"]))
        doc_times.setdefault(doc_id, []).append(row["fetched_at"])

        if len(doc_ids_by_key) % 500 == 0:
            db.commit()
    db.commit()

    # doc.first_seen_at / last_modified_at 取合并行的最小/最大 fetched_at
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

    # ---- membership(items.domain_id=1,合并去重后 assigned_by='ai') ----
    seen_pairs: set[tuple[int, int]] = set()
    for row in src.execute("SELECT * FROM items WHERE domain_id IS NOT NULL"):
        url_key = normalize_url(row["url"])
        doc_id = doc_ids_by_key.get(url_key)
        pair = (doc_id, row["domain_id"])
        if doc_id is None or pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        db.add(Membership(doc_id=doc_id, domain_id=row["domain_id"],
                          assigned_by="ai", created_at=row["fetched_at"]))
    db.commit()
    counts["membership"] = db.query(Membership).count()

    # ---- reading(有任何阅读状态的行) ----
    for row in src.execute(
        "SELECT * FROM items WHERE is_read=1 OR is_favorite=1 "
        "OR user_rating IS NOT NULL OR note IS NOT NULL OR is_dismissed=1"
    ):
        url_key = normalize_url(row["url"])
        doc_id = doc_ids_by_key.get(url_key)
        if doc_id is None:
            continue
        existing = db.get(Reading, doc_id)
        if existing is not None:
            continue
        db.add(Reading(
            doc_id=doc_id, is_read=row["is_read"] or 0,
            is_favorite=row["is_favorite"] or 0, rating=row["user_rating"],
            note=row["note"], is_dismissed=row["is_dismissed"] or 0,
            updated_at=row["saved_at"] or row["fetched_at"],
        ))
    db.commit()
    counts["reading"] = db.query(Reading).count()

    # ---- run_log(sync_logs → kind='fetch';旧 jobs 0 行) ----
    for row in src.execute("SELECT * FROM sync_logs"):
        db.add(RunLog(
            kind="fetch", pipe_id=PIPE_MERGE.get(row["source_id"], row["source_id"]),
            pipe_name=row["source_name"], trigger=row["trigger"],
            status="done" if row["ok"] else "failed",
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
    target_engine.dispose()

    print("迁移完成。行数统计:")
    for k in sorted(counts):
        print(f"  {k:12s} {counts[k]}")
    print(f"  合并重复行   {dedup_rows}(url_key 归并)")
    print(f"  kind 分布    {kind_counts}")


if __name__ == "__main__":
    migrate()
