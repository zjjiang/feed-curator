"""幂等迁移:paper 表增加源侧信号列 extra(LongText JSON)。

MySQL 上查 information_schema,列不存在才 ALTER;SQLite 由 create_all
建表自带,直接跳过。用法:

    set -a && source .env && set +a
    uv run python scripts/migration/add_paper_extra.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import text

from app.db import engine

COLUMNS = {
    "extra": "LONGTEXT NULL",
}


def column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    ).scalar()
    return bool(row)


def main() -> int:
    dialect = engine.url.get_backend_name()
    if dialect != "mysql":
        print(f"方言 {dialect} 无需迁移(create_all 自带新列),跳过")
        return 0
    with engine.begin() as conn:
        for name, ddl in COLUMNS.items():
            if column_exists(conn, "paper", name):
                print(f"paper.{name} 已存在,跳过")
                continue
            conn.execute(text(f"ALTER TABLE paper ADD COLUMN {name} {ddl}"))
            print(f"paper.{name} 已添加")
    print("迁移完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
