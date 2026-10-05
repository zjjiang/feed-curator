## 1. 聚合服务(TDD:先写测试)

- [x] 1.1 写 `tests/services/test_stats_service.py` 失败用例:窗口解析与默认回退(非法 days、跨度 > 92 天回退 14 天)
- [x] 1.2 写失败用例:每日总览聚合(按 first_seen_at 本地日归属;kind 细分;分析 ok 只算 status='ok';重判取最新;reading 缺行视为 0;dismiss 文档仍计入)
- [x] 1.3 写失败用例:渠道转化(discovery 归属;多渠道重复计数;零入库渠道不出现;已读率计算)
- [x] 1.4 写失败用例:领域转化(membership 归属;多领域重复计数;零归属领域不出现)
- [x] 1.5 实现 `app/services/stats_service.py`:输入 (db, start_ts, end_ts),输出三块聚合 dict;最新 ok 判定用 GROUP BY doc_id + MAX(id) 模式;全部内存聚合,不写库

## 2. 路由与页面

- [x] 2.1 `app/web/pages.py` 新增 `GET /admin/stats`:解析 days / start / end 参数(非法回退),调用 stats_service,渲染模板
- [x] 2.2 新建 `app/web/templates/stats.html`:日期范围切换(7/14/30 预设 + 自定义表单)+ 三块纯表格,风格对齐 ops.html;空数据显示空表
- [x] 2.3 `app/web/templates/layout.html` 管理区导航加"统计"入口

## 3. 验证与收尾

- [x] 3.1 `uv run pytest` 全绿,`uv run pytest --cov=app` 覆盖率 ≥ 80%
- [x] 3.2 手动核对:对照 /admin 现有当日累计数,抽查某天入库/已读数与 DB 直查一致
- [x] 3.3 schema 双方言确认(stats 只读查询在 MySQL 与 SQLite 均可执行,无方言函数)
