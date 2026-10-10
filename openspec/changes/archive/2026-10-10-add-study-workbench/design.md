# Design: add-study-workbench

## Context

静态原型 `data/gen_workbench.py`(不进仓库运行时)已验证交互动线:双栏布局、卡片流键盘导航、翻过即已读、localStorage 存 UI 态。本变更把它转正为服务端页面,关键差异是**已读/收藏的真相源从 localStorage 换成 `reading` 表**。

## Goals / Non-Goals

- Goals:一次载荷、客户端过滤的流畅双模式;阅读状态服务端持久;与现有 web 层约定(无构建步骤、Jinja2、内联 JS/CSS)一致。
- Non-Goals:不做增量/分页载荷(7 天约 1.5MB,一次下发可接受);不做正文全文阅读(详情给摘要+要点,精读跳原文);不做评分/笔记(reading 表支持但 UI 暂不暴露)。

## Decisions

### D1. 载荷组装独立成 service(`app/services/study_service.py`)

端点与页面都是薄壳,组装逻辑(最新 ok analysis 去重、领域/管道名映射、reading 状态合并)收在一个纯函数 `collect_study_docs(db, days)` 里,便于直接单测。

### D2. 「最新 ok analysis」取法

analysis 是 append-only,force 重判会产生多条 ok 记录。载荷按 `MAX(id) GROUP BY doc_id` 取每文档最新一条,join 时不得产生重复文档行(规格 Scenario「多条 ok 判定不产生重复」)。

### D3. 前端状态分层

- **服务端真相**:已读/收藏 → `POST /api/docs/{doc_id}/reading`(复用现有端点,零新写路径);页面加载时从载荷读初始状态。
- **浏览器本地**:视图模式、卡片流当前位置、过滤器(localStorage)——只影响 UI,不影响数据。

### D4. 页面模板独立于 layout.html

layout 的 `<main>` 有 920px 定宽,与满屏双栏冲突。study.html 用独立整页模板(自带精简顶栏与返回链接),JS/CSS 全部内联(仓库无构建步骤、无静态资源目录约定)。layout.html 仅加一个导航链接。

### D5. 键盘交互收口

全局 keydown 监听,输入框聚焦时与详情浮层打开时短路;卡片流的「翻过即已读」只发生在 next 动作(含点原文),prev 不标已读——与微信「看过才算」语义一致。

## Risks / Trade-offs

- 一次载荷在 days=30 时可达 ~6MB:接受(局域网/本机使用);days 上限 30 由 API 校验兜底。
- 客户端过滤在千级文档上无性能问题;若未来窗口拉长到万级,再引入分页/游标。
