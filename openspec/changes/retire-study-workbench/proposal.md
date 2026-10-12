# retire-study-workbench

## Why

读高星文档、追踪情报属于调研流,职责已确认收敛到 deputy 的军情处;feed-curator 收敛为纯供给方(采集 → 判定 → 检索),经 OpenClaw agent 以 MCP/HTTP 中转消费。学习台(/study)与该职责划分冲突,且与 deputy 侧模块重复,应下线。

## What Changes

- 删除 `GET /api/study/docs` 端点、`/study` 页面路由、`study.html` 模板、`study_service.py` 与导航入口。
- 删除 `tests/test_study.py`。
- 能力 `study-workbench` 退役:两条 requirement(学习载荷 API、学习工作台页面)整体 REMOVED,归档同步时主 spec 删除。
- `reading` 表及其全部读写路径**原样保留**(服务订阅流 `/`、详情页 `/docs/{id}`、`/admin/stats`;与学习台无专属耦合)。
- MCP 工具面(`search_docs`/`get_doc` 等)零改动——军情处/讲武堂经 agent 依赖它们消费语料。
- CLAUDE.md 增加与 deputy 侧呼应的状态归属边界声明。

非目标:deputy 侧任何改动(其 add-learning 提案中「study-workbench 管积压阅读」一行由 deputy 侧自行微调);reading 状态的跨系统打通(将来如需,走 agent 跨系统取数,不搬状态)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `study-workbench`:两条 requirement 整体 REMOVED,能力退役(REMOVED delta 带 Reason/Migration;归档同步时删除主 spec)。

## Impact

- 代码:`app/services/study_service.py`、`app/web/templates/study.html`、`tests/test_study.py` 删除;`app/main.py`、`app/web/pages.py`、`app/web/templates/layout.html` 各删一处;`CLAUDE.md` 布局与边界声明更新。
- 数据:零 schema 变更;`reading` 表数据不动。
- 下游:仅 `study.html` 消费 `/api/study/docs`,无其他调用方;deputy/agent 消费走 `/api/docs`、`/api/docs/{doc_id}` 与 MCP,不受影响。
