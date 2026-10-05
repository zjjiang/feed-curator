## Context

系统数据模型已完备支撑转化统计:`doc.first_seen_at`(入库时间)、`analysis`(追加写,生效判定 = 最新一条 `status='ok'`)、`reading`(1:1 懒创建,已读/收藏/评分)、`discovery`(pipe × doc 采集溯源)、`membership`(doc × domain 多对多)。`/admin` 现有页面只展示 run_log 流水与当日累计,无跨天、无转化视角。

约束:个人系统,日增几十上百条,14 天窗口内 doc 至多数千行;生产 MySQL、测试 SQLite 双方言;前端一贯是服务端渲染纯表格,无前端构建链。

## Goals / Non-Goals

**Goals:**

- `/admin/stats` 一页看三块:每日总览、渠道转化、领域转化。
- 漏斗口径统一:cohort = 入库日,终点 = 已读 / 收藏 / AI≥4 星。
- 支持近 7 / 14 / 30 天与自定义起止日期。
- 纯只读,实时聚合,可单测(不依赖 MySQL 方言函数)。

**Non-Goals:**

- 不做图表(纯表格,不引前端库)。
- 不做预聚合表、定时任务、物化视图。
- 不做用户评分(rating)与阅读行为时间线——`reading.updated_at` 是最后操作时间,无法还原"哪天读的",本期不统计"按阅读日"视角。
- 不做导出、不做分页(窗口内数据量小,单页渲染)。

## Decisions

1. **Python 内存聚合,不用 SQL 日期函数。**
   一次查询拉回窗口内 docs(带 kind、first_seen_at),再批量查 analysis(最新 ok 判定)、reading、discovery、membership,在 Python 里按本地日期分组聚合。
   - 为何不 SQL `GROUP BY`:MySQL `FROM_UNIXTIME` 与 SQLite `date(ts,'unixepoch')` 方言不同,SQL 聚合需按方言分支且难测;数据量小,内存聚合无性能问题。
   - 为何不全量拉:限定 `first_seen_at` 窗口,量级可控。

2. **最新 ok 判定一次查出,不用相关子查询。**
   复用首页 `_latest_ok_analysis_ids` 的模式:`GROUP BY doc_id` 取 `MAX(id)` 再 join;AI≥4 星即该行 `stars >= 4`。

3. **多渠道 / 多领域重复计数,是特性不是 bug。**
   discovery 本身是"哪个管道采到了哪个文档"的多对多溯源;渠道表回答"从这个口进来的内容转化如何",同一 doc 走两个口就在两行各计一次。领域同理(membership 多对多)。日总览以 doc 为单位,天然去重。

4. **时间窗参数:`days` 预设 + `start`/`end` 自定义(YYYY-MM-DD),窗口 = 本地日 [start 00:00, end+1 00:00)。**
   参数校验放路由层(非法值回退 14 天);聚合函数只接受 (start_ts, end_ts) epoch 边界,保持纯函数可测。

5. **代码分层沿用现有惯例。**
   `app/services/stats_service.py` 承载全部查询与聚合(输入 session + 窗口,输出 dict 结构,不碰 request);`pages.py` 只做参数解析与模板渲染;`stats.html` 继承 layout.html。导航链接加在 layout.html 管理区。

## Risks / Trade-offs

- [数据量增长后全窗口内存聚合变慢] → 当前量级(数千行)远未触及;若未来需要,可加 `days` 上限(如 ≤ 92 天)兜底,本期设上限。
- [渠道/领域重复计数让各块加总 ≠ 日总览] → 属口径特性,页面注释说明"渠道/领域按归属重复计数"。
- [reading 懒创建,未操作过的 doc 三层终点均为 0] → 符合漏斗语义(未读);查询用 outer join / `IN` 批查缺行视为全 0。
- [自定义日期跨度过大] → 路由层限制跨度 ≤ 92 天,超出回退默认。
