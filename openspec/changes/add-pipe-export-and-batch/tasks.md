## 1. 导出服务(TDD:先写测试)

- [x] 1.1 写失败用例:md 生成 —— 六类分组、行字段完整、派生管道标注领域、config 只出摘要字段、含生成时间与总数统计、空管道时各空类不出现
- [x] 1.2 实现 `app/services/pipe_export.py` 的纯函数 `render_pipes_md(db) -> str`
- [x] 1.3 写失败用例:git 推送 —— 无变化跳过、非 main 分支跳过、提交只含 docs/pipes.md(subprocess mock,不真连网)
- [x] 1.4 实现 `export_pipes(db, trigger)`:render + git 检测/提交/推送(30s 超时),run_log kind='export',进程内单飞锁

## 2. 批量拉取(TDD:先写测试)

- [x] 2.1 写失败用例:批量执行 —— 顺序拉取选中管道、单管失败记 last_error 继续、重入拒绝、空选择拒绝
- [x] 2.2 实现 `app/services/pipe_batch.py`:`start_batch_fetch(pipe_ids)` 后台线程 + 单飞(参照 repo_enrich 的 readme 模式)

## 3. 路由与页面

- [x] 3.1 `pages.py`:POST `/admin/pipes/batch-fetch`(接收 ids,空/busy 提示回跳)、POST `/admin/pipes/export`(手动导出)、/admin 按钮路由
- [x] 3.2 `pipes.html`:复选框列 + 全选 + "更新选中"表单;`ops.html`:手动操作区加"导出源清单"按钮
- [x] 3.3 fetch tick 接入每日导出 due 检查(仿 repo_refresh 的 maybe 模式)

## 4. 验证与收尾

- [x] 4.1 `uv run pytest` 全绿,覆盖率 ≥ 80%
- [ ] 4.2 本地实测:导出按钮真实 push 一次,检查 GitHub 上 docs/pipes.md 渲染与内容正确
- [x] 4.3 实测批量拉取:选 3 根管道更新,确认 run_log 三条、busy/空选择路径正常
