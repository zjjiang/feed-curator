"""Dry-run：对现有 items 按 URL 判定实体类型，输出分布与 HN 来源明细。

不写库，只读 SQLite 源库并打印结果。用法：
    uv run python -m scripts.migration.dry_run_kind_distribution
"""

import sqlite3
from collections import Counter
from pathlib import Path

from app.utils.doc_kind import detect_kind

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "feed-curator.db"

HN_SOURCE_HINT = "hacker"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT i.id, i.url, s.name FROM items i JOIN sources s ON s.id = i.source_id"
    ).fetchall()
    conn.close()

    kind_counter: Counter[str] = Counter()
    hn_counter: Counter[str] = Counter()
    hn_repo_ids: list[int] = []
    hn_paper_ids: list[int] = []

    for item_id, url, source_name in rows:
        kind = detect_kind(url)
        kind_counter[kind] += 1
        if HN_SOURCE_HINT in source_name.lower():
            hn_counter[kind] += 1
            if kind == "repo":
                hn_repo_ids.append(item_id)
            elif kind == "paper":
                hn_paper_ids.append(item_id)

    print(f"总行数: {len(rows)}")
    print(f"kind 分布: {dict(kind_counter)}")
    print()
    print(f"Hacker News 来源分布: {dict(hn_counter)}")
    print(f"  HN 判为 repo: {len(hn_repo_ids)} 个")
    print(f"  HN 判为 paper: {len(hn_paper_ids)} 个")


if __name__ == "__main__":
    main()
