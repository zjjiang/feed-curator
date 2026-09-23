"""对 MySQL 目标库跑一轮 article 正文补全(8.6)。

用法(项目根目录):
    DATABASE_URL="mysql+pymysql://..." uv run python -m scripts.migration.run_fulltext_backfill [limit]
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    from app.services.fulltext_backfill import run_backfill

    stats = run_backfill(limit=limit, sleep_seconds=1.0, batch_size=50)
    print("补全完成:", stats)


if __name__ == "__main__":
    main()
