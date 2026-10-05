## Why

管理后台 `/admin` 目前只有近 50 条 run_log 流水与当日累计数,看不到"每天进来多少、真正读掉了多少"的转化情况。渠道和领域越加越多,需要一份按天 × 渠道 × 领域拆分的转化统计,来判断哪些 pipe / domain 值得保留、摄入量是否超过处理能力。

## What Changes

- 管理后台新增统计页 `/admin/stats`,支持近 7 / 14 / 30 天与自定义日期范围。
- 页面分三块纯表格(无图表、不引前端库):
  - **每日总览**:按入库日统计入库数(细分 paper/repo/article)、分析 ok 数、已读、收藏、AI≥4 星。
  - **渠道转化**:按 discovery 归属渠道,统计入库 → 分析 ok → 已读 → 收藏 → AI≥4 星及已读率。
  - **领域转化**:按 membership 归属领域,同一漏斗口径。
- 口径:cohort 按 doc 入库日(`first_seen_at`,本地时区按天);AI≥4 星取该 doc 最新一条 `status='ok'` 判定的 `stars`;同一 doc 被多个渠道采集在各渠道各计一次;doc 属于多个领域在各领域各计一次。
- 不新建表、不加定时任务,纯只读实时聚合。
- layout.html 管理区导航加入口。

## Capabilities

### New Capabilities

- `admin-stats`: 管理后台按日 × 渠道 × 领域的转化统计页(口径、查询聚合、页面展示)。

### Modified Capabilities

(无 —— 现有 spec 的需求均不变;页面入口只是 layout 的展示层改动,不构成 spec 级需求变化。)

## Impact

- 新增 `app/services/stats_service.py`(查询与内存聚合)、`app/web/templates/stats.html`。
- 修改 `app/web/pages.py`(新增 `/admin/stats` 路由)、`app/web/templates/layout.html`(导航)。
- 新增 `tests/services/test_stats_service.py` 等测试;纯只读,无 schema 变更、无新依赖。
