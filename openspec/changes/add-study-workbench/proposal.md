# Proposal: add-study-workbench

## Why

内容进料(AI 判定覆盖 100%)与消费严重脱节:近 7 天 1635 篇新文档,已读/收藏均为 0——微信推送渠道只适合路上碎片化扫一眼,无法承载电脑端的系统学习。已有一个静态原型(`data/gen_workbench.py`,本地生成单文件 HTML)验证了「卡片流逐篇翻 + 列表筛选」双模式的学习动线,但快照数据不实时、阅读状态只存浏览器 localStorage、与 `reading` 表完全脱节。

## What Changes

- **新增学习工作台页面 `GET /study`**:独立的满屏双栏页(左侧过滤器 + 右侧内容区),两种视图模式——卡片流(逐篇翻,`←`/`→`/`j`/`k` 键盘导航,翻过即自动标已读,`f` 收藏)与列表(全部文档一页浏览,点开详情浮层)。模式选择、流内阅读位置存 localStorage;已读/收藏走服务端 `reading` 表,跨浏览器同步。
- **新增学习载荷 API `GET /api/study/docs?days=N`**(N 默认 7,限 1–30):一次性返回窗口期内文档的完整学习载荷(星级、摘要、要点、领域、来源管道、已读/收藏状态),按星级降序、时间降序。客户端本地做星级/领域/类型/未读/收藏/搜索过滤,不重复请求服务端。
- **复用现有 `POST /api/docs/{doc_id}/reading`** 写已读/收藏,不新增写路径。
- **布局导航增加「学习台」入口**(layout.html)。

不改变:分析契约、判定流程、采集管道、MCP 工具面、现有 `/` 订阅流与管理后台。

## Capabilities

### New Capabilities

- `study-workbench`: 电脑端学习工作台——双模式阅读动线(卡片流/列表)、学习载荷 API 契约、阅读状态与 `reading` 表的同步语义。

### Modified Capabilities

- 无(仅 layout.html 增加一个导航链接,不构成行为变更)。

## Impact

- 新增:`app/services/study_service.py`(载荷组装)、`app/web/templates/study.html`、`tests/test_study.py`
- 修改:`app/main.py`(挂 `GET /api/study/docs`)、`app/web/pages.py`(挂 `GET /study`)、`app/web/templates/layout.html`(导航链接)
