"""Dry-run：按归一化 URL 对现有 items 分组，输出会被合并的重复组，供人工核对。

不写库，只读 SQLite 源库并打印结果。用法：
    uv run python scripts/migration/dry_run_url_dedup.py
"""

import sqlite3
from collections import defaultdict
from pathlib import Path

from app.utils.url_key import normalize_url

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "feed-curator.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, url FROM items ORDER BY id").fetchall()
    conn.close()

    groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for item_id, url in rows:
        groups[normalize_url(url)].append((item_id, url))

    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}
    total_dup_rows = sum(len(v) for v in duplicate_groups.values())

    print(f"总行数: {len(rows)}")
    print(f"归一化后唯一 URL 数: {len(groups)}")
    print(f"重复组数: {len(duplicate_groups)}")
    print(f"重复组涉及总行数: {total_dup_rows}")
    print()

    for url_key, items in sorted(duplicate_groups.items()):
        print(f"--- {url_key} ({len(items)} 行) ---")
        for item_id, original_url in items:
            marker = "" if original_url == url_key else f"  [原始: {original_url}]"
            print(f"    id={item_id}{marker}")
    print()


if __name__ == "__main__":
    main()
