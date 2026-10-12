## 1. REST 参数（TDD：Red → Green）

- [x] 1.1 测试（Red）：`GET /api/docs?sort=stars` 星级降序且无判定沉底；`?min_stars=4` 只留 ≥4 且排除未判定；`?min_stars=0` / `sort=bad` → 422；默认不传 = 现状 time 序回归
- [x] 1.2 `app/main.py`（Green）：list_docs_api 增 `sort`/`min_stars` Query；`_latest_ok_analysis_ids` 抽到共享处供 pages/main 复用；跑 `uv run pytest` 全绿
## 2. 收尾

- [x] 2.1 `openspec validate add-docs-star-sort` 通过；CLAUDE.md 若有 API 清单则同步（无则跳过）
