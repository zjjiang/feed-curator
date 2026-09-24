# Design: collect-hot-repos

## Context

rebuild 后 repo 已是三类实体之一，基础设施大量就绪：`Repo` 表（含预留的 `readme_text` 列）、`refresh_repo()` 刷新契约、`doc_fields.build_detail` 的 repo 字段分流、AI 分析读取 `readme_text` 作为判定正文、`resolve_fetch_config` 预留了 github 派生管道钩子（当前实现是简单的空格拼接，需替换）。唯一缺的是适配器本体与热度语义。

2026-09-24 网络实测（本机，中国网络环境）：

| 目标 | 结果 |
|---|---|
| `api.github.com/search/repositories` | HTTP 200，0.76s（匿名） |
| `github.com/trending`（热门页 HTML） | 15s 超时，0 字节 |

配额（官方文档）：Search API 匿名 10 次/分、带 token 30 次/分；Core API 匿名 60 次/时、带 token 5000 次/时。

用户已确认的产品口径：**只收 AI 相关**（由领域派生查询实现）；**新爆款 + 增量追踪**双轨；**有 token**（用户自行申请，public 只读无需 scope）；feed 卡片显示 GitHub 真实星数与正增量。

## Goals / Non-Goals

**Goals:**

- github 派生管道按领域自动发现「近期爆火」仓库并入库，复用既有采集/去重/判定链路
- 新仓库首个 AI 判定前即持有 README，判定质量不打折
- 库内仓库每日刷新指标，星标增量可见
- 无 token 时全链路可降级运行，有 token 时配额充裕

**Non-Goals:**

- 全局共享热门管道（无领域过滤的 trending 全量入库）——违背「只收 AI 相关」
- 语言过滤（`language:` 限定符）——首周观察产量后再决定
- 星标历史快照表（任意时间窗增量）——当前只需「相对上次刷新」一档
- ETag 条件请求省配额——token 配额下无必要
- feed「按热度」排序——保持时间/AI 星级两种排序
- github 仓库的 git clone / 代码分析

## Decisions

### 推演一：数据源选择

| 候选 | 结论 | 依据 |
|---|---|---|
| A. 抓 github.com/trending 页 | **死** | 实测 15s 超时；且无官方 API，HTML 结构随时变 |
| B. 第三方（OSS Insight、RSSHub 镜像等） | **死** | 域名在国内普遍不可达；RSSHub 本地实例已停；引入外部单点 |
| C. GitHub Search API | **活** | 实测 200/0.76s；官方、稳定、参数化程度高 |

**选择 C。** 代价是 Search API 无法表达「一段时间内新增星标」，这个缺口由推演二的分解补上。

### 推演二：「热门」语义还原

GitHub trending 页是黑盒（≈ 近几天 star/fork 增速按仓库年龄归一）。Search API 能表达的排序只有 `stars`（累计）、`updated`、`forks`——没有「增速」。把「热门」分解为两个可查询/可计算的近似：

```
热门 = 新爆款(发现问题)          +  存量翻红(追踪问题)
       |                            |
       v                            v
  created:>=T-7d                对库内 repo 周期采样
  stars:>=50                    delta = stars_now - stars_prev
  sort=stars desc               (检索接口查不了,只能本地算)
       |                            |
       +--------> 订阅流 <----------+
```

- **新爆款**：`created:>=7天前 stars:>=50 sort=stars`。时间窗切断老项目，星标阈值滤掉玩具仓库。量级实测：全语言全领域 `stars:>100` 一周 159 个；阈值 50 放宽到约每周数百，经领域关键词过滤后剩个位数到数十，信号密度合适。
- **存量翻红**：Search API 无解，唯一途径是本地采样——这就是 `refresh_repo` 契约与 `repo-stars-tracking` 能力的由来。它只能覆盖已入库仓库（无发现能力），恰与新爆款互补：发现负责「进得来」，追踪负责「涨没涨」。

**参数默认值**：窗口 7 天、阈值 50 星、`per_page=30`。三者全部进管道 `config`（`window_days` / `min_stars` / `per_page`），上线后按实际产量调。

### 推演三：AI 相关过滤放在哪一层

| 层 | 做法 | 评估 |
|---|---|---|
| 查询层 | 派生管道：领域关键词 → 检索词 | 精准前置；依赖关键词质量；**选它为主** |
| 判定层 | 全量入库后 LLM 打相关性分 | 费 token；非 AI 仓库先污染库 |
| 展示层 | 全量入库靠 domain 筛选掩盖 | = 全收，违背「只收 AI 相关」 |

**选择查询层**，即启用派生 github 管道：领域「具身智能」的关键词 `[具身智能, 人形机器人, "Embodied AI", VLA, ...]` 组装成检索词，天然限定 AI 范围。判定层的 AI 归域（既有机制）继续兜底做精细归类——两层不冲突。

注意与 `resolve_fetch_config` 现状的差异：现实现是 `" ".join(keywords)`（空格拼接 = GitHub 语义下「全部包含」，过严），必须替换为推演七的组装规则。

### 推演四：README 补全时机（本设计最关键的时序约束）

分析作业对每个文档**只判一次**（`analysis` 追加式，生效判定 = 最新 ok 行；`select_doc_ids` 只选未判定文档）。repo 的判定正文 = `readme_text`，为空时 AI 只能凭 200 字 description 判定，且**事后 README 到位不会触发重判**——判薄了就是永久薄。

| 时机 | 评估 |
|---|---|
| A. 采集周期内对新 repo 同步补全 | 首判必有正文；30 个新 repo ≈ 30 次 Core 请求 ≈ 10s，在采集线程内可接受。**选它** |
| B. 仿 fulltext_backfill 慢速补 | 与分析周期（300s）赛跑大概率输，首判仍薄 |
| C. 不抓 README | 判定质量差，违背 readiness 初衷 |

**选择 A + 兜底**：抓取失败（网络/404）不阻塞入库，README 留空；`/ops` 提供手动补抓按钮（对 `readme_text IS NULL` 的 repo 限速分批，复用 fulltext_backfill 的模式）。API 用 `GET /repos/{owner}/{repo}/readme` + `Accept: application/vnd.github.raw+json` 直取原始 markdown（api.github.com 可达，无需碰被墙的 raw.githubusercontent.com）。

### 推演五：增量存储与调度

**存储**：

| 方案 | 评估 |
|---|---|
| A. `Repo` 加 `stars_prev` / `stars_gained` 两列 | 一档增量够用；无新表、无额外查询；**选它** |
| B. 快照表 `repo_star_snapshot` | 可算任意窗口，但需要新表+清理策略+查询 join；记为未来扩展 |

增量语义：`stars_gained = 本次 stars - 上次 stars`（如实含负值），`stars_prev` 存上次值；首次刷新两者皆 NULL。刷新间隔每日 → 增量近似「日增」，卡片展示 `(+120)` 即此值。

**调度**：不新增第三个 APScheduler 作业，在既有 fetch tick（60s）里加一个到期检查——距上次刷新运行 > 24h 且无刷新进行中 → 启动（模块级锁防重入，与 fetcher/runner 的单飞约束同风格）。`run_log` 记 `kind='refresh'`（列宽 String(16) 足够）。

**配额核算**（有 token）：库内 repo 按每日 +30 增长，一年 ≈ 1.1 万次 Core 请求/天 ≪ 5000/时；搜索每管道每周期 1 次 ≪ 30/分。无 token 时：README 优先（新 repo，占用 60/时的大部分），刷新降级为单轮 ≤50 个、最久未刷新优先轮转。

### 决策六：代码结构

```
app/services/github_client.py   纯 HTTP:httpx + token + 限流退避 + MockTransport 可测
   ├- search_repositories(query, per_page) -> list[dict]
   ├- get_repo(owner, name) -> dict
   └- get_readme(owner, name) -> str        (raw markdown)
app/adapters/github.py          GitHubAdapter:组装最终查询串,dict -> FetchedItem
app/jobs/fetcher.py             github 管道入库后,对新 repo 调 repo_enrich
app/services/repo_enrich.py     README 补全:enrich_new_repos() / backfill_readmes()
app/services/repo_refresh.py    日刷新:due 检查 + run_refresh(算增量,走 refresh_repo)
app/main.py                     fetch tick 里挂 refresh 到期检查
```

`github_client` 不碰 DB（同 `wewe_client` 先例）；adapter 不碰 DB（铁律）；写库仍全走 `upsert_doc` / `refresh_repo` 单一写入口。`refresh_repo` 扩展两个可选参数（`stars_prev` / `stars_gained`），保持「repo 表唯一写入口」契约。

### 决策七：关键词 → 检索串组装规则

```
输入: ["具身智能", "Embodied AI", "VLA", "Robot Learning", ...]
  1. 丢弃非 ASCII 词条            -> 具身智能 掉(中文在 GitHub 检索无收益)
  2. 含空格的词条加引号(短语匹配)  -> "Embodied AI" "Robot Learning"
  3. 词条截到前 6 个(GitHub 限制单条检索最多 5 个 OR 算子,实测 7 词即 422)
  4. OR 连接                      -> VLA OR "Embodied AI" OR "Robot Learning"
  5. 与窗口/阈值限定符合并:
     created:>=2026-09-17 stars:>=50 sort=stars-desc per_page=30
  6. 总长截到 256 内(按词条顺序保序截断,保证不产生残缺引号)
```

全部 ASCII 关键词被丢弃时（某领域只配了中文词），派生管道本次跳过采集并在 `last_error` 说明原因，MUST NOT 用空查询打接口。

### 推演六（真机实测勘误）：默认窗口与阈值修正

冒烟实测推翻了推演二的默认参数（7 天 / 50 星）——**对小众领域交集为空**：

| 检索（2026-09-24 实测） | 结果 |
|---|---|
| 具身智能关键词组 + `created:>=7d` + `stars:>=50` | **0 条** |
| 同关键词组 + `created:>=90d` + `stars:>=30` | **114 条**（utopia、opendm、giga-world-1 等真领域仓库） |
| `VLA` + `created:>=90d` | 4474 条；仅 `created:>=7d` + `stars:>=50` 同样为 0 |
| 纯限定符（无关键词）`created:>=7d` + `stars:>=50` | 425 条（每周全球新爆款总量） |

结论：领域相关 ∧ 一周新仓 ∧ 50 星 的三者交集在细分领域几乎恒为空。**默认参数改为 90 天 / 30 星**（管道 config 仍可调），语义从「本周爆款」调整为「近一季度新起、星标领先的领域仓库」，按星标降序取前 30 即领域内的上升头部。若将来新增泛 AI 大领域，可在管道配置里收窄回 7 天/50 星。

同场实测还确认了两个接口硬约束：单条检索**最多 5 个 AND/OR/NOT 算子**（7 词条即 422，故词条截到 6 个）；**不支持括号分组**（括号被当字面量，检索词全部失效——组合限定符时不加括号，依赖接口对限定符的全局作用语义，实测全局生效）。

## Risks / Trade-offs

- **[GitHub 检索相关性不可控]** OR 语义可能召回边缘仓库 → 星标阈值兜底 + per_page=30 限流；上线首周人工 review 产量，调 `window_days`/`min_stars`。
- **[星标农场/炒作仓库]** 短期刷星仓库混入 → 下游 AI 评级与「忽略」消化；增量追踪反而在二次刷新时暴露异常（暴涨后停滞）。
- **[api.github.com 国内偶发抖动]** → 全链路幂等：upsert 按 url_key 去重、discovery 唯一约束；失败进 `last_error`/`run_log`，下一周期自然重试；不做进程内重试风暴。
- **[repo 的 sort_time = pushed_at]** 老仓库因最近 push 浮到时间排序顶部 → 可接受（feed 另有 AI 星级排序）；若观感差，后续可给 repo 换 `first_seen_at` 排序，属独立调整。
- **[无 token 时配额争抢]** README 与刷新共享 60 次/时 → 优先级固定：README（新 repo）> 刷新（轮转限 50）；采集本身只占搜索配额，不受影响。
- **[派生管道数量增长]** 每个领域一条 github 管道 = 每周期一次搜索请求 → 30 次/分上限支撑约 30 条管道同分钟触发；间隔错开由既有调度保证，短期无风险。

## Migration Plan

1. `scripts/migration/20260924_repo_star_delta.py`：幂等 ALTER TABLE（查 information_schema 确认列不存在才加 `stars_prev`/`stars_gained`，均 nullable）。SQLite 走 `create_all` 不需要脚本。
2. 部署顺序：加列（在线、无锁风险）→ 发代码 → 用户在 `.env` 配 `GITHUB_TOKEN` → admin 创建「具身智能」的派生 github 管道（间隔建议 ≥360 分钟）。
3. 回滚：停用/删除 github 管道即止血；两列可安全 DROP，无数据依赖。

## Open Questions

- 无。首周观察项（不阻塞）：产量与噪音比、是否需要语言过滤、阈值是否调整。
