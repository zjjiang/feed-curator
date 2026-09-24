# Tasks: collect-hot-repos

## 1. 数据层

- [x] 1.1 `app/models/doc.py` 的 `Repo` 增加 `stars_prev`（Integer, nullable）与 `stars_gained`（Integer, nullable）两列；跑 `uv run pytest tests/models/test_schema.py` 确认 MySQL/SQLite 双方言编译通过
- [x] 1.2 新增幂等迁移脚本 `scripts/migration/20260924_repo_star_delta.py`（查 information_schema，列不存在才 ALTER）；先写脚本内自检逻辑，再对本地 MySQL 执行并复查 `SHOW COLUMNS FROM repo`

## 2. GitHub 客户端

- [x] 2.1 先写 `tests/services/test_github_client.py`（httpx.MockTransport）：搜索解析、限流 403/429 停止退避、readme 直取 raw markdown、404 返回 None、token 请求头注入；再实现 `app/services/github_client.py`（`search_repositories` / `get_repo` / `get_readme`，读 `GITHUB_TOKEN` 环境变量，超时与退避）
- [x] 2.2 验证匿名与带 token 两种模式下客户端对真实接口的单次调用可用（手动 curl 或临时脚本，不做进测试套件）

## 3. 适配器与查询组装

- [x] 3.1 先写 `tests/adapters/test_github.py` 与查询组装单测：ASCII 过滤（中文词丢弃）、多词短语加引号、OR 连接、窗口/星标限定符、256 截断不出残缺引号、全中文关键词产出空查询信号；再实现 `app/adapters/github.py` 的 `GitHubAdapter`（dict → `FetchedItem`：`external_id=full_name`、`url=html_url`、`published_at=pushed_at`、指标进 `meta`）
- [x] 3.2 `app/jobs/fetcher.py` 的 `resolve_fetch_config`：github 派生管道改为传递关键词列表（替换空格拼接），共享管道用 `config.query`；补对应单测（派生随关键词实时变化、共享不受影响）
- [x] 3.3 `app/adapters/__init__.py` 注册 `github`；用 fixture 搜索响应跑通 `fetch_source` 到 `upsert_doc` 的入库单测（指标落 Repo 一等列、sort_time=pushed_at、跨管道去重只加 discovery）

## 4. README 补全

- [x] 4.1 先写 `tests/services/test_repo_enrich.py`：新 repo 补 README 成功填充、失败保留 NULL 且不阻塞、已非空不重复抓、补全不写 discovery/采集记录；再实现 `app/services/repo_enrich.py`（`enrich_new_repos` 供 fetcher 调用、`backfill_readmes(limit)` 供运维用，限速 1s/请求）
- [x] 4.2 `app/jobs/fetcher.py` 的 github 分支入库后调用 `enrich_new_repos`（仅对本次 `doc_created` 且 kind=repo 的条目）；补 fetcher 集成单测（mock 客户端）

## 5. 星标刷新作业

- [x] 5.1 先写 `tests/services/test_repo_refresh.py`：增量计算（有前值/首次 NULL/星标下降记负值）、`refresh_repo` 扩展参数落列、判定数据不被触碰（analysis/membership/reading 不变）、`run_log` 出现 `kind='refresh'` 记录、无凭据单轮 ≤50 按最久未刷新轮转、失败隔离；再实现 `app/services/repo_refresh.py`（`refresh_due` / `run_refresh`，模块级锁防重入）
- [x] 5.2 `app/writer.py` 的 `refresh_repo` 增加 `stars_prev`/`stars_gained` 可选参数（保持既有调用兼容），补单测
- [x] 5.3 `app/main.py` fetch tick 挂接到期检查（>24h 且无刷新进行中 → 启动）；`/ops` 增加「立即刷新星标」「补抓 README」按钮与对应 POST 路由，补路由测试

## 6. Web 展示

- [x] 6.1 `app/web/pages.py` 订阅流查询 join `Repo`（stars/stars_gained/language 进卡片数据），`index.html` 卡片显示 `★ 12,345 (+120)` 样式（正增量才显示增量、与 AI 评级视觉可区分、无数据显示留空）；补 pages 单测
- [x] 6.2 `doc.html` 仓库详情在既有 `★{stars}` 旁补增量显示；补模板渲染测试
- [x] 6.3 `/admin/pipes` 表单支持 github 类型：类型下拉加 `github`、派生管道绑定领域、config 字段（`window_days`/`min_stars`/`per_page`，带默认值）；`source_service.create_pipe` 校验 github 配置；补表单与校验测试

## 7. 收尾

- [x] 7.1 CLAUDE.md 更新：github 管道启用说明、`GITHUB_TOKEN` 配置说明、run_log kind 列表补 `refresh`
- [x] 7.2 全量 `uv run pytest` 通过且 `uv run pytest --cov=app` 覆盖率 ≥ 80%
- [ ] 7.3 真机冒烟：采集/README/星标展示/迷你刷新已验证(30 入库、29 README、卡片 ★ 渲染、3/3 刷新)；仅剩 AI 判定产出领域归属一环——待 .env 补 DEEPSEEK_API_KEY(现为空串)与 GITHUB_TOKEN 后触发 /api/analyze/run 复验
