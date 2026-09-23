## Why

当前 schema 在 DDL 层写死了「一篇文章属于一个源」这个假设（`items.source_id NOT NULL` + `UNIQUE(source_id, external_id)`），导致用户无法以「领域」为单位订阅：想让 36Kr 的具身文章进入具身领域，唯一办法是再建一个「具身版 36Kr」源。库中已有实证——`36kr.com/feed` 和 `huxiu/article` 各被建了 2 个源，arXiv cs.AI 被查了 2 次；同一篇论文经 3 个源进来存了 3 行，全库按原始 URL 分组有 7 组 15 行重复，归一化（去 fragment 与追踪参数）后为 11 组 23 行。而这些为过滤而生的专用源几乎不产出：「具身文章 · 36Kr」抓 2 次入库 0 篇，同期通用「36氪」入库 52 篇。

代价是 37 篇标题命中具身关键词的文章躺在领域之外。同时 `items` 把三类本质不同的东西（论文/仓库/文章）挤在一张表里：`content_html` 和 `cover_image_url` 对 350 行 paper/repo 永久为 NULL，`word_count` 在 repo 是 28（README 摘要）、在 article 是 1182（正文），语义不同却共用一列；丢失的 github adapter 只能把仓库的 `pushed_at` 硬塞进 `published_at`。

现在动手的窗口期：**AI 判定层完全是空的**（2335 篇 `ai_score` 全为 NULL，`jobs` 表 0 行，AI 从未运行过），改判定契约不需要重跑任何已有结果，重复行合并也无已读/收藏状态需要调和（全库仅 1 篇有 `user_rating`）。

## What Changes

- **BREAKING** 领域成为订阅单位：用户订阅 `domain`，源退化为底层管道。领域归属从「源级继承」改为「文档级 AI 判定」，且为多对多（一篇 VLA 论文可同属具身智能与 AI）。
- **BREAKING** `item` 单表拆为类表继承结构：`doc`（身份层，共有最简信息）+ `paper` / `repo` / `article`（各自完整的实体表，共享 `doc.id` 主键空间）。三类实体的独有属性成为原生列（如 `repo.stars` 可建索引），不再埋在 JSON 里。
- **BREAKING** 全局去重键从 `(source_id, external_id)` 改为归一化 URL `doc.url_key`。`external_id` 形态不统一无法胜任（github 是纯数字、rss 有 381 条 tag URI、wechat 为私有格式），而 url 在 2335 行中全部非空；363 条带 query 参数，故必须归一化。
- 实体类型由内容判定而非管道推导：Hacker News（rss 管道）抓回的 github 链接判为 `repo`、arxiv 链接判为 `paper`，不再全部误判为文章。按此规则对现有数据实测（规则已由单元测试锁定），分布为 `article 1902 / paper 295 / repo 138`。
- 新增 article 正文补全：`rss.py` 只解析 feed 自带内容，导致 1902 篇文章中有 1077 篇正文不足 500 字（其中 Hacker News 856 篇），技术/商业判定只能覆盖 43%。抓取原文的逻辑此前存在于已丢失的 `archive_service.py`，本次重建为独立服务并接入采集链路。
- 文章再分「技术/商业」（`article.kind_tag`）。收藏不是频道而是用户行为，归入 `reading` 表。
- AI 判定改为追加写（`analysis` 表），保留历史版本以便调整 prompt 后对比新旧判定质量。
- 新增 `document_link`（文档间关系，如论文 implements 仓库）与 `suggestion`（agent 建议，需用户确认才生效）两张表，为后续 agent 预留接入点。
- `source` 表更名为 `pipe`，`domain_id` 可空以区分共享管道（有全量流，多领域共用）与派生管道（如 github search，query 本身即管道）。
- 合并重复管道：「具身文章 · 36Kr」并入「36氪」、「具身文章 · 虎嗅」并入「虎嗅」、「具身论文 · AI」并入「arXiv cs.AI」。
- `Setting.categories` 退役——它原本就是「主题领域」的角色，已被 `domain` 取代，且当前为空数组，退役零成本。
- `Job` 与 `SyncLog` 合并为 `run_log`（字段高度重叠，运维看板当前需查两张表）。

## Capabilities

### New Capabilities
- `domain-subscription`: 领域作为订阅单位。领域的定义（名称/描述/关键词）、启用状态，以及领域视图如何混排展示论文/仓库/文章。
- `document-model`: 文档实体模型。`doc` 身份层与 `paper`/`repo`/`article` 三类实体的字段归属、实体类型判定规则、基于归一化 URL 的全局去重。
- `pipeline-ingestion`: 管道采集。共享管道与派生管道的区分、采集调度、采集溯源（`discovery`），以及派生管道的 query 如何由领域关键词生成。
- `ai-analysis`: AI 判定。判定契约（摘要/要点/领域归属/文章子类/星级）、追加写语义与生效版本判定、失败与越界的防御处理。
- `reading-state`: 用户阅读状态。已读/收藏/评分/笔记/忽略，与文档内容分离存储。

### Modified Capabilities
<!-- 无。本仓库此前没有 openspec/specs/ 下的既有能力定义。 -->

## Impact

**数据库**：目标库改为本机 MySQL 实例（非 `db-mp` 容器，另一独立运行的 MySQL），新建 `feed_curator` 库并全量建 `domain` / `pipe` / `doc` / `paper` / `repo` / `article` / `discovery` / `analysis` / `membership` / `document_link` / `reading` / `suggestion` / `run_log` 十三张表。**本次视为重建，不是渐进迁移**：MySQL 目标库不建 `sources` / `items` / `jobs` / `sync_logs` / `settings` / `domains` 六张旧表结构；迁移脚本只读 SQLite 源库（`data/feed-curator.db`），核对全部计数断言通过后，SQLite 源文件直接删除（保留迁移前的 `.bak-<日期>` 备份作为唯一回滚手段）。

**迁移风险**：2335 行、22MB，其中 1959 篇 rss 文章发布时间回溯到 2026-05-14。RSS 仅提供最近 N 条，这些数据一旦损坏无法重抓。迁移须先备份 SQLite 源文件、脚本幂等可重跑、写入 MySQL 目标库后核对计数再切换 `DATABASE_URL`。

**代码**：`app/models.py`、`app/db.py` 重写；`app/jobs/fetcher.py` 改为写入 `doc` + 实体表（单一写入口，避免孤儿 doc）；`app/ai/client.py` 判定契约变更；`app/web/pages.py` 与模板按领域视图重构；`app/mcp_server.py` 工具签名随之调整。`app/adapters/` 与 `app/utils/` 基本可直接移植。

**已丢失代码**：`app/adapters/github.py`、`app/services/archive_service.py`、`app/services/briefing_service.py` 源文件已不存在，仅余 `__pycache__` 字节码（已备份至 `/tmp/feed-curator-pyc-backup/`）。其中 `archive_service.py` 的抓取与解析逻辑本次重建为 `app/services/fulltext.py`；`github.py` 不重写（见下）。

**运行环境**：迁移前 `.env` 不存在，`DATABASE_URL` 与 `DEEPSEEK_API_KEY` 均缺失，故数据实际只在 SQLite（`data/feed-curator.db`）中，AI 从未运行过。`feed-curator` 容器与 CLAUDE.md 提到的 `db-mp` 容器均未在运行——CLAUDE.md 描述的部署状态已失效。本次改用本机另一个独立运行的 MySQL 实例作为迁移目标库，`.env` 的 `DATABASE_URL` 将指向该实例。

**范围边界**：**repo 与 paper 的采集与字段补全划归另立项目**。本次只保证两张实体表就绪、`upsert_doc` 的对应分支可用、既有 295 篇 paper 与 138 个 repo 正确迁入，并把 article 链路做完整。故 `github.py` 本次不重写，库中 2 个 github 派生管道迁移时置为停用。

**非目标**：本次不实现 agent（另立 change，见 `tasks.md` 中的 todo）；不处理 16 篇「标题相同但 url 不同」的跨站转发去重（留给 `document_link` 的 `duplicate` 关系事后处理）；不修复 wechat 管道（`we-mp-rss` 容器 3 个月前已 Exited(137)，502 错误源于此，属部署问题）。
