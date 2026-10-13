# intel-export-api 变更（delta）

## ADDED Requirements

### Requirement: 文档流导出含仓库星标

`GET /api/docs` 返回的每个文档 SHALL 包含 `gh_stars` 与 `gh_stars_gained` 字段：仓库（repo）实体取自其星标追踪数据，其余实体类型两字段 SHALL 为 null。字段含义与 `/docs/{id}` 详情页一致（星标数为当前值，增量为相对上次刷新）。

#### Scenario: 仓库条目带星标

- **WHEN** 文档流中包含一个已完成星标追踪的仓库文档
- **THEN** 该条目 `gh_stars` 为其当前星标数，`gh_stars_gained` 为相对上次刷新的增量（可为 0）

#### Scenario: 非仓库条目星标为空

- **WHEN** 文档流中包含论文或文章文档
- **THEN** 该条目 `gh_stars` 与 `gh_stars_gained` 均为 null

### Requirement: 统计导出端点

系统 SHALL 提供 `GET /api/admin/stats` 只读端点：接受 `days`（如 7/14/30）或 `start`/`end` 日期参数，返回与 `/admin/stats` 页面同源同口径的统计 JSON（每日总览 daily、渠道转化 pipes、领域转化 domains）。该端点 SHALL 仅做读取与序列化，SHALL NOT 改变统计口径；既有 `GET /api/stats`（全局运维计数）SHALL 保持不变。

#### Scenario: 按预设天数取统计

- **WHEN** 客户端请求 `GET /api/admin/stats?days=30`
- **THEN** 返回最近 30 天的 daily/pipes/domains 统计 JSON，数据与同窗口的 `/admin/stats` 页面一致

#### Scenario: 自定义起止日期

- **WHEN** 客户端请求 `GET /api/admin/stats?start=2026-10-01&end=2026-10-07`
- **THEN** 返回该日期区间的统计 JSON

#### Scenario: 非法窗口参数宽容回退

- **WHEN** 客户端传入无法解析的日期或非法 days
- **THEN** 与 `/admin/stats` 页面行为一致，回退默认窗口，SHALL NOT 报错中断
