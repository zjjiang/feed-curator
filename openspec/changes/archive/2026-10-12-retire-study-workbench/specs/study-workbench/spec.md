## REMOVED Requirements

### Requirement: 学习载荷 API

**Reason**:读高星文档、追踪情报的职责已收敛到 deputy 的军情处(调研流),feed-curator 收敛为纯供给方(采集 → 判定 → 检索)。学习载荷聚合端点与 deputy 侧模块职责重复。

**Migration**:读高星文档由 deputy 军情处承接;语料供给走既有 `GET /api/docs`、`GET /api/docs/{doc_id}` 与 MCP 工具 `search_docs`/`get_doc`(经 OpenClaw agent 中转)。`POST /api/docs/{doc_id}/reading` 与 `reading` 表原样保留,服务订阅流与详情页的已读/收藏/屏蔽。

### Requirement: 学习工作台页面

**Reason**:同上——`/study` 页面的卡片流阅读视图与 deputy 军情处的调研流职责重复;两套系统不做重复的学习/阅读入口。

**Migration**:阅读入口为 feed-curator 订阅流 `/`(含已读/收藏/星级过滤)与 deputy 军情处视图;原文已读/收藏状态继续记录在 feed-curator `reading` 表(状态跟着实体走,永不搬进 deputy)。
