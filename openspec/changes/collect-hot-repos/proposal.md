# Proposal: collect-hot-repos

## Why

rebuild 之后仓库（repo）已是三类实体之一，`Repo` 表、`refresh_repo` 写入契约、AI 分析读 `readme_text` 的链路全部就绪，但系统没有任何仓库采集来源——`github` 管道类型只有类型注释与派生查询钩子（`resolve_fetch_config`），没有适配器。用户希望 GitHub 上的热门 AI 相关仓库自动流入订阅流，与论文、文章混排阅读。2026-09-24 实测：`github.com/trending` 页面在本机网络完全超时不可达，而 `api.github.com` 稳定可用（搜索请求 0.76s 返回 200），官方 Search API 是唯一可行数据源。

## What Changes

- 新增 `GitHubAdapter`（`app/adapters/github.py`）：对接 GitHub Search API，产出 `FetchedItem`（owner/name/stars/forks/language/topics 等进 `meta`），不触碰 DB。
- 启用 **github 派生管道**作为主采集路径：查询由领域关键词组装（仅取 ASCII 关键词、多词加引号、OR 连接、总长 ≤256），叠加热度时间窗（默认近 90 天创建）与最低星标（默认 ≥30），即「该领域近期爆火的仓库」。
- **README 补全**：新入库仓库在采集周期内同步抓取 README 填充 `repo.readme_text`，使首次 AI 判定即有充足正文；失败不阻塞入库，留待 `/ops` 手动补抓。遵循 `pipeline-ingestion`「采集不承担实体补全」语义，补全不记为采集。
- **星标增量追踪**：新增每日刷新作业（`app/services/repo_refresh.py`），刷新库内仓库的 stars/forks/open_issues/pushed_at，计算相对上次的星标增量；`Repo` 表新增 `stars_prev`/`stars_gained` 两列（附幂等迁移脚本）。`run_log` 新增 `kind='refresh'`。
- **GITHUB_TOKEN 支持**：从环境变量读取 Personal Access Token（用户自行申请，public 只读无需 scope）；有 token 时搜索 30 次/分 + Core 5000 次/时，无 token 降级（搜索 10 次/分可用，README 补抓与日刷新限小批量轮转）。
- **Feed 卡片展示**：订阅流与领域视图的工程项目卡片显示 GitHub 真实星数与正增量（`★ 12,345 (+120)`）；「按星级」排序仍指 AI 评级，语义不冲突。
- `/admin/pipes` 表单与 `/ops` 页面相应支持 github 管道类型的创建/展示与手动触发（README 补抓、星标刷新）。

## Capabilities

### New Capabilities

- `repo-discovery`：github 管道的发现语义——Search API 查询组装（关键词派生 + 热度窗口 + 星标阈值）、仓库条目到 `Repo` 一等列的映射、README 补全流程、凭据与限流约束。
- `repo-stars-tracking`：星标增量追踪——周期刷新库内仓库指标、相对上次的增量计算与存储、刷新限速分批与无 token 降级、刷新与采集/判定的隔离。

### Modified Capabilities

- `domain-subscription`：「领域视图混排」要求扩展——工程项目卡片 MUST 显示 GitHub 真实星数，有正增量时 MUST 同时显示增量；与 AI 1-5 评级的展示 MUST 可区分。

## Impact

- **新增**：`app/adapters/github.py`、`app/services/repo_refresh.py`、`scripts/migration/` 幂等加列脚本。
- **修改**：`app/adapters/__init__.py`（注册）、`app/jobs/fetcher.py`（README 补全钩子）、`app/main.py`（日刷新调度）、`app/services/source_service.py`（github 管道创建）、`app/web/pages.py` + `app/web/templates/`（卡片星数、/ops 触发）、`app/models/doc.py`（`Repo.stars_prev`/`Repo.stars_gained`）、`tests/`。
- **配置**：`.env` 新增可选 `GITHUB_TOKEN`；`app/config`（或等价读取处）补充读取。
- **外部依赖**：仅 GitHub REST API v3（`api.github.com`，已实测可达）；不引入新 Python 包（复用 `httpx`）。
- **风险**：GitHub API 国内偶发抖动——采集幂等（URL 去重 + discovery 唯一约束），失败进 `pipe.last_error`/`run_log`，下周期自然重试；Search API 查询 256 字符上限——组装器负责截断。
