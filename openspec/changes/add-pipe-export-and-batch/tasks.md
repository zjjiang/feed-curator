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
- [x] 4.2 本地实测:worktree(main)真实 run_export 一次,push 成功,GitHub API 确认内容正确
- [x] 4.3 实测批量拉取:选 3 根管道更新,确认 run_log 三条、busy/空选择路径正常

## 5. 内容分类 + JSON 交换格式(评审后扩展)

- [x] 5.1 写失败用例:内容大类规则(论文源不埋没、厂商/社区/媒体/公众号/手工各自归类、config category 覆盖、规则顺序)
- [x] 5.2 写失败用例:pipes.json 构建(version/exported_at/pipes 数组、domain 存名字、round-trip 可导入)
- [x] 5.3 实现分类规则 + build_pipes_json,run_export 改为双文件(docs/pipes.md + docs/pipes.json)提交推送
- [x] 5.4 写失败用例:导入(合法 JSON 创建、type+name 去重、未知领域跳过、非法 JSON 拒绝且零写入、裸数组兼容)
- [x] 5.5 实现 `app/services/pipe_import.py`(边界校验 + 去重 + create_pipe)
- [x] 5.6 POST /admin/pipes/import 路由 + pipes.html 导入表单(粘贴 JSON);更新导出相关测试
- [x] 5.7 全量测试 + 真实重导出(双文件推送,GitHub 确认 JSON 可下载且分类正确)

## 6. 去掉 Markdown,只留 JSON(评审后收敛)

- [x] 6.1 更新规格/设计:导出物收敛为 docs/pipes.json,category 进 JSON 每条字段
- [x] 6.2 pipe_export 删除 md 渲染,run_export 单文件;导出测试全部改 JSON 断言
- [x] 6.3 分支合入 main 后删除仓库中的 docs/pipes.md
- [x] 6.4 全量测试 + 真实重导出确认 GitHub 上只剩 pipes.json
