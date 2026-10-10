## Purpose

学习工作台把近期高判定价值文档组织成可专注阅读的学习视图:一次性取回
窗口期学习载荷,以卡片流或列表模式供用户翻阅、标注已读与收藏,并以
过滤器收敛范围。阅读状态服务端持久,跨设备一致。

## Requirements

### Requirement: 学习载荷 API

系统 SHALL 提供 `GET /api/study/docs?days=N` 端点,一次性返回近 N 天(N 默认 7,MUST 限制在 1–30)文档的完整学习载荷,按星级降序、时间降序排列。

载荷中每篇文档 MUST 包含:doc id、实体类型(kind)、URL、标题、发布时间、星级、摘要、要点列表、领域名列表、来源管道名列表、已读与收藏状态。判定结果 MUST 取该文档最新一条 `status='ok'` 的 analysis(append-only 语义下不得因多条记录产生重复文档)。未判定文档 MUST 包含在内(星级为 0),不得因判定缺失而丢弃。

#### Scenario: 载荷覆盖窗口期并按星级排序

- **WHEN** 客户端请求 `GET /api/study/docs?days=7`
- **THEN** 响应 MUST 只含 `sort_time` 在近 7 天内的文档
- **AND** 文档 MUST 按星级降序排列,同星级的按时间降序
- **AND** 每篇文档 MUST 携带领域名与来源管道名

#### Scenario: 多条 ok 判定不产生重复

- **WHEN** 某文档存在多条 `status='ok'` 的 analysis 记录(force 重判)
- **THEN** 载荷中该文档 MUST 只出现一次,且字段取自其中最新一条

#### Scenario: days 越界被拒绝

- **WHEN** 客户端请求 `days=0` 或 `days=31`
- **THEN** 响应 MUST 为 422 校验错误

### Requirement: 学习工作台页面

系统 SHALL 提供 `GET /study` 页面,以满屏双栏布局呈现学习视图:左侧为过滤器(星级、领域、类型、只看未读、只看收藏、搜索),右侧为内容区。页面 MUST 提供两种视图模式且可随时切换:

- **卡片流模式**:一次呈现一篇文档;用户通过 `←`/`→`(或 `j`/`k`)翻篇;**翻到下一篇时,当前篇自动标记为已读**;打开原文链接亦视为已读;`f` 键收藏当前篇。
- **列表模式**:窗口期文档单页列出,点击展开详情浮层(摘要、要点、原文链接、已读/收藏操作)。

两种模式 MUST 共用同一套过滤器。视图模式与卡片流阅读位置 SHALL 持久化在浏览器 localStorage。

#### Scenario: 卡片流翻篇即已读

- **WHEN** 用户在卡片流中从第 i 篇翻到第 i+1 篇
- **THEN** 第 i 篇 MUST 被标记为已读(POST 到 reading API)
- **AND** 再次进入页面时 MUST NOT 重新出现于「只看未读」视图

#### Scenario: 阅读状态服务端持久

- **WHEN** 用户在工作台标记某文档已读或收藏
- **THEN** 该状态 MUST 写入 `reading` 表
- **AND** 换浏览器或清空 localStorage 后 MUST 仍保持

#### Scenario: 过滤器双模式共用

- **WHEN** 用户设置「4★+」「只看未读」过滤
- **THEN** 卡片流与列表模式 MUST 呈现同一批文档
