# Proposal: expose-intel-stats-and-repo-stars

## Why

deputy 军情处的「翻阅」面正在与 feed-curator 对齐，缺三块数据/行为，都在 feed-curator 侧：

1. **GitHub 星标数**：repo 条目的 `stars`/`stars_gained` 在 repo 实体表里已有，但 `GET /api/docs` 没返回，deputy 情报流显示不了 GitHub 星标。
2. **统计面板**：`/admin/stats` 的统计（每日总览/渠道转化/领域转化）只有管理后台 HTML 页，没有 JSON API，deputy 想内嵌「统计」视图拿不到数据。
3. **文章评级基线**：AI 判定 prompt 对文章（资讯）没有基线倾斜，资讯类常被打 3 星，用户期望文章默认按 4 星基线评（值得一读），明显平庸才降档。

## What Changes

- `GET /api/docs` 响应的每个 doc 增加 `gh_stars`、`gh_stars_gained`（仅 repo 实体有值，其余为 None）——从 repo 实体表带出，逻辑与 `/docs/{id}` 页面一致。
- 新增 `GET /api/admin/stats?days=7|14|30`（或 start/end 日期），复用 `stats_service.collect_stats`，原样返回 JSON（daily/pipes/domains 结构），与 `/admin/stats` 同源同口径。既有 `GET /api/stats`（全局运维计数、无消费方）保持不动，避免双形状端点。只读，无鉴权面变化（localhost 前提不变，红线④不触碰）。
- AI 判定 SYSTEM_PROMPT 评级条目增加文章基线：资讯类文章默认按 4 星基线评——一般资讯即 4 星，明显平庸/标题党才 3 星及以下，突出者 5 星；论文与工程项目的严格评级不变。已入库的历史判定不变，只影响之后的判定。

## Capabilities

### New Capabilities

- `intel-export-api`: 面向 deputy 的只读导出 API——`/api/docs` 的 gh_stars 字段与 `/api/stats` 统计端点。

### Modified Capabilities

（无——AI 判定基线属提示词行为调整，不改变既有 spec 需求；如现有 specs 里有分析判定相关需求会一并核对，不新增需求条目。）

## Impact

- `app/main.py`：`_doc_dict` 补两字段；新增 `/api/stats` 端点（复用 parse_window + collect_stats）。
- `app/ai/client.py`：SYSTEM_PROMPT 评级条目措辞。
- 测试：`/api/docs` gh_stars 字段、`/api/stats` 结构与窗口参数、prompt 含基线措辞的断言。
- deputy 侧消费（渲染 GitHub 星标、内嵌统计视图）由 deputy 仓库自行实现，不在本 change 范围。
