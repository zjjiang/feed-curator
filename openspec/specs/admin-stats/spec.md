## Purpose

管理后台转化统计:按日 × 渠道 × 领域展示 内容摄入 → AI 判定 → 阅读 → 收藏 → AI 高评分 的转化漏斗,为调整 pipe / domain 提供数据依据。纯只读实时聚合,不建表、不写库、不依赖前端图表库。

## Requirements

### Requirement: 统计页按日期范围展示每日总览
`/admin/stats` SHALL 支持近 7 / 14 / 30 天预设与自定义起止日期(YYYY-MM-DD),并按入库日(本地时区)展示每日总览表,列包括:日期、入库数、论文数、项目数、文章数、分析 ok 数、已读数、收藏数、AI≥4 星数。日期跨度 MUST 限制在 92 天以内,非法或超限参数 SHALL 回退为默认 14 天。

#### Scenario: 默认访问
- **WHEN** 用户访问 `/admin/stats` 不带参数
- **THEN** 展示近 14 天的每日总览表

#### Scenario: 自定义日期范围
- **WHEN** 用户访问 `/admin/stats?start=2026-09-01&end=2026-09-10`
- **THEN** 展示 2026-09-01 至 2026-09-10(按本地日,含两端)的每日总览表

#### Scenario: 非法参数回退
- **WHEN** 用户访问 `/admin/stats?days=abc` 或跨度超过 92 天
- **THEN** 回退为近 14 天,页面正常渲染

#### Scenario: 按入库日归属
- **WHEN** 某文档 `first_seen_at` 为 10 月 1 日 23:50,其分析在 10 月 2 日完成且被收藏
- **THEN** 该文档的全部指标(入库、分析 ok、已读、收藏、AI≥4 星)计入 10 月 1 日一行

### Requirement: 每日总览的漏斗口径
每日总览 SHALL 以 doc 为计数单位(天然去重),各列定义 MUST 为:入库数 = 窗口内 `first_seen_at` 的 doc 数(含被 dismiss 的);论文/项目/文章数 = 按 `doc.kind` 细分;分析 ok 数 = 该 doc 存在 `status='ok'` 判定;已读数 = `reading.is_read=1`;收藏数 = `reading.is_favorite=1`;AI≥4 星数 = 该 doc 最新一条 ok 判定的 `stars >= 4`。

#### Scenario: 分析失败不计入分析 ok
- **WHEN** 某文档只有 `status='failed'` 的判定记录
- **THEN** 该文档计入入库数,不计入分析 ok 数

#### Scenario: 重判取最新判定
- **WHEN** 某文档先被判 3 星、后被重判为 5 星
- **THEN** AI≥4 星计数按最新 ok 判定(5 星)计入

#### Scenario: 被 dismiss 的文档仍计入统计
- **WHEN** 窗口内某文档被用户 dismiss
- **THEN** 该文档仍计入入库与后续各列(dismiss 不影响统计口径)

### Requirement: 渠道转化统计
统计页 SHALL 展示渠道转化表:以 `discovery` 将窗口内 doc 归属到 pipe,按渠道汇总入库数、分析 ok 数、已读数、收藏数、AI≥4 星数与已读率(已读/入库,百分比)。同一 doc 被多个渠道采集时 MUST 在每个渠道各计一次。窗口内零入库的渠道 SHALL 不出现在表中。

#### Scenario: 多渠道重复计数
- **WHEN** 同一文档被 pipe A 与 pipe B 都采集过
- **THEN** pipe A 与 pipe B 的入库数各计 1

#### Scenario: 已读率计算
- **WHEN** 某渠道入库 10、已读 4
- **THEN** 该渠道已读率显示 40.0%

### Requirement: 领域转化统计
统计页 SHALL 展示领域转化表:以 `membership` 将窗口内 doc 归属到 domain,列与口径同渠道转化表。同一 doc 属于多个领域时 MUST 在每个领域各计一次。窗口内零归属的领域 SHALL 不出现在表中。

#### Scenario: 多领域重复计数
- **WHEN** 同一文档同时属于领域 LLM 与 Agent
- **THEN** 两个领域的入库数各计 1

### Requirement: 统计为纯只读实时聚合
统计页 SHALL 只执行只读查询实时聚合,不 SHALL 新建表、不 SHALL 写入任何数据、不 SHALL 引入前端图表库。管理后台导航(layout.html 管理区)SHALL 提供指向 `/admin/stats` 的入口。

#### Scenario: 窗口内无数据
- **WHEN** 选定日期范围内没有任何入库文档
- **THEN** 页面正常渲染,各表为空、无错误

#### Scenario: 导航入口
- **WHEN** 用户打开任意管理后台页面
- **THEN** 管理区导航中出现"统计"链接,指向 `/admin/stats`
