## Context

动机见 `proposal.md - Why`。本节只列影响技术选型的现状约束。

**当前代码状态**：`app/adapters/github.py`、`app/services/archive_service.py`、`app/services/briefing_service.py` 源文件已丢失（仅余字节码，已备份至 `/tmp/feed-curator-pyc-backup/`）。`models.py`、`db.py`、`main.py`、`pages.py`、`mcp_server.py`、`fetcher.py`、`runner.py` 被回退到旧版本。**fetch 链路当前是断的**：`ADAPTERS` 注册表中无 `github`，而库中有 2 个 github 管道，采集时会抛未知类型。`:9003` 进程已退出。因此本次重构没有「服务中断」代价——它已经断了。

**数据库现状与代码不一致**：库中 `items` 表已有 `domain_id`、`is_dismissed`、`saved_at`、`note`、`user_rating`、`ai_keypoints` 六列，`sources` 有 `domain_id`，`domains` 表有 1 行完整数据——这些是被回退的 `models.py` 定义的，DDL 已落库但代码里无定义。

**运行环境（CLAUDE.md 的描述已失效）**：`.env` 不存在，故 `DATABASE_URL` 未设置，`app/db.py` 静默回退到本地 SQLite——**迁移前数据只在 `data/feed-curator.db`（22MB）里**。`feed-curator` 容器不存在（`docker ps -a` 无此容器），此前跑的是本机 uvicorn 进程。CLAUDE.md 描述的 `db-mp` 容器亦未在运行；`we-mp-rss` 容器已退出 3 个月。`DEEPSEEK_API_KEY` 同在缺失的 `.env` 中，这是 AI 从未运行过的直接原因。

**本次改用本机 MySQL（非 `db-mp` 容器），且视为重建而非渐进迁移**：本机另有一个独立于 `db-mp` 的 MySQL 实例在跑（`root` 可登录，`information_schema` 中未见 `feed_curator` 库），已新建 `feed_curator` 库作为迁移目标。旧 `data/feed-curator.db`（SQLite）只作为迁移脚本的只读数据源。**MySQL 目标库只建 13 张新表，不迁旧表结构；迁移验证通过后 SQLite 源文件直接删除**——不是"新旧共存观察一周"，本次目标是彻底切换到新库，不保留旧数据形态。schema MUST 同时在两种方言下成立——本次写 MySQL，但保持 SQLite 兼容以备日后本地开发或测试仍需回退到 SQLite（`app/db.py` 的既有回退逻辑本身不变）。

**实体类型的实际分布**：按 URL 判定规则（`app/utils/doc_kind.py`，13 条单元测试锁定）对现有 2335 行实测，结果为 `article 1902 / paper 295 / repo 138`。paper 与 repo 数量高于按管道类型统计的 280/70，差额来自 Hacker News 抓回的 83 个链接（68 个 github 仓库根 + 15 个 arxiv 论文页）——这正是「实体类型由内容判定」要修正的误判。另有 17 个 HN github 链接指向议题/文件页，正确地判为 article。

**article 正文的实际质量**：1902 篇 article 中，仅 825 篇正文超过 500 字，1077 篇不足 500 字（其中 Hacker News 856 篇，HN 另有 43 篇正文实际充足）。原因是 `app/adapters/rss.py` 只解析 feed 自带内容（`entry.content` / `summary`），不抓取 `entry.link` 指向的原文。抓取原文的能力此前存在于已丢失的 `archive_service.py`（字节码可证：BeautifulSoup + httpx + SSRF 防护），但只挂在「手工存入 URL」入口，未接入 RSS 采集链路。

**数据规模与不可再生性**：2335 行文档，其中 1959 篇 rss 文章发布时间回溯至 2026-05-14。RSS 仅提供最近 N 条，这批数据损坏后无法重抓。AI 判定层为空（`ai_score` 全 NULL，`jobs` 0 行），故判定契约变更无历史包袱。

## Goals / Non-Goals

**Goals:**

- schema 在 SQLite 与 MySQL 下语义一致，沿用现有 `LongText` 变体与 utf8mb4 约定
- 迁移可重跑、可回滚：从 SQLite 源库读、写入 MySQL 目标库，不原地改写 SQLite 的 `items` 表
- 归属、判定、关系、阅读状态四类引用保持真外键，不使用多态外键
- 为后续 agent 预留 `document_link` 与 `suggestion` 接入点，使 agent 实现为纯增量

- article 链路完整可用：采集、正文补全、技术/商业判定、领域归属、阅读状态

**Non-Goals:**

- 不在本次引入 ORM 之外的查询层或迁移框架（Alembic 等）；迁移用一次性脚本
- 不优化 2335 行规模下无实际影响的查询性能
- 不重写 `app/adapters/rss.py`、`arxiv.py`、`wechat.py` 与 `app/utils/`——直接移植
- 不实现 agent 本体（见 `tasks.md` 末尾 todo）
- **不实现 repo 与 paper 的采集与字段补全**——另立项目负责（见决策 11）。本次只保证两张实体表就绪、`upsert_doc` 的对应分支可用，以及既有的 295 篇 paper 与 138 个 repo 正确迁入

## Decisions

### 1. 类表继承：`doc` 身份层 + 三张实体表

```
doc                                身份层，共有最简信息
  id            PK                 统一标识空间
  kind          paper|repo|article
  url_key       UNIQUE             归一化 URL，全局去重键
  url, title
  sort_time     INDEX              跨实体统一排序（见决策 3）
  first_seen_at
  last_modified_at

paper  id PK FK doc.id       repo  id PK FK doc.id        article  id PK FK doc.id
  abstract                     owner, name                  author
  content_text                 description                  description
  authors      JSON            readme_text                  content_text  LongText
  categories   JSON            stars       INDEX            content_html  LongText
  pdf_url                      forks, open_issues           cover_image_url
  arxiv_id, version            language    INDEX            word_count
  submitted_at                 topics      JSON             kind_tag  tech|business
                               license                      published_at
                               pushed_at                     source_meta   JSON
                               refreshed_at
```

**为何不用单表 + kind 字段**：`content_html` 与 `cover_image_url` 对 350 行 paper/repo 永久为 NULL；`word_count` 在 repo 均值 28（README 摘要）、在 article 均值 1182（正文），同列不同义；`repo.stars` 埋在 JSON 里无法原生建索引。

**为何不用三张完全独立的表**：`membership`、`analysis`、`document_link`、`reading` 四张表都需引用「任意一类实体」，独立表会迫使它们全部改用 `(target_kind, target_id)` 多态外键，数据库层无法保证引用完整性——而 `document_link` 是 agent 的核心表。保留 `doc` 这一层，四张表全为真外键。

**代价**：插入一篇文档 = 两次 insert，须同事务；可能产生「孤儿 doc」（`kind='paper'` 但 `paper` 表无记录），数据库无法反向约束。缓解见决策 6。

### 2. 去重键用归一化 URL，不用 `external_id`

`external_id` 形态不统一，跨管道不可比：

```
arxiv   http://arxiv.org/abs/2606.02578v1    URL
github  1010767195                           纯数字，70 条
rss     tag:www.ruanyifeng.com,2026:...      tag URI，381 条
wechat  3585795950-2247491132_1              平台私有格式，26 条
```

而 `url` 在 2335 行中全部非空。按未归一化的原始 `url` 直接分组，重复组数为 7 组（共 15 行，此前误记为 16 行，已修正）。

**归一化后（dry-run 实测）进一步发现 4 组、8 行额外重复**：`simonwillison.net` 的同一篇文章在 RSS 里出现两条记录，一条带 `#atom-everything` 锚点、一条不带——按原始 URL 不相等，但显然是同一篇文档。归一化后总计 **11 组、23 行**重复（脚本：`scripts/migration/dry_run_url_dedup.py`）。这证明了归一化的价值不止于 query 参数：**fragment（`#` 及之后部分）MUST 整体剥离**，它从不承载文档身份，只标记页内位置。

归一化规则：小写 host、统一 scheme、去末尾斜杠、**剥离 fragment**、按白名单剥离追踪参数（当前白名单仅 `utm_*` 前缀——363 条带 query 参数的 URL 中它是唯一稳定重复、跨源出现的追踪参数；`ref` / `spm` 等在现有数据中未实际出现，不预先加入白名单，见 Open Questions）。**363 条 URL 带 query 参数、多条 URL 带 fragment，故归一化不是可选项。** 承载文档身份的参数（如 `?id=123`、`?v=abc`、`?p=123`）MUST 保留——规则采用「白名单剥离」而非「全部剥离」，避免误伤。

`(pipe_id, external_id)` 保留为 `discovery` 表上的唯一约束，用于单管道增量判断，沿用现有语义。

### 3. `doc.sort_time`：为排序牺牲语义纯度

三类实体的时间语义不同：论文是投稿时间、文章是发表时间、仓库是最后推送时间（且一直在变）。丢失的 github adapter 曾把 `pushed_at` 硬塞进 `published_at`（字节码可证），正是这一混淆的产物。

**决策**：`doc` 上放 `sort_time`，各实体保留语义准确的字段（`submitted_at` / `published_at` / `pushed_at`）。

**为何不叫 `published_at`**：名字必须诚实说明它是「用于排序的时间」。**为何不省掉它**：否则混排排序需 `COALESCE(p.submitted_at, r.pushed_at, a.published_at)`，无法建单一索引。**代价**：repo 刷新时须同步更新 `doc.sort_time` 与 `repo.pushed_at` 两处——由单一写入口保证（决策 6）。

### 4. `content_text` 不上提到 `doc`

三类都有「正文」，但同名不同物：paper 均长 196（abstract）、repo 均长 28（README 摘要）、article 均长 1182（rss）至 3012（wechat）。上提会让全文检索把 README 与文章混排。各表分别命名为 `abstract` / `readme_text` / `content_text`。

`doc` 上除 `id / kind / url_key / url / title / sort_time / first_seen_at / last_modified_at` 外不放其他字段——`author` 也不共有（repo 的 author 语义是 owner）。

### 5. `analysis` 追加写，`membership` 与 `article.kind_tag` 物化

`analysis` 永不 UPDATE，生效版本 = 该 `doc_id` 下最新一条 `status='ok'`。**理由**：技术/商业这一刀切在哪、领域关键词够不够，都需要反复调提示词并对比新旧判定——覆盖写会让对比无从进行。这是本次选择「换 schema」而非「就地改」的直接收益。

`membership` 与 `article.kind_tag` 是由 `analysis` 推导出的物化结果，`analysis` 是真相来源。**理由**：领域视图每次查询都 join 最新 analysis 过于昂贵。物化带来「两处一致性」问题，由判定写入路径统一维护。

`membership.assigned_by` 区分 `ai` 与 `manual`。重新判定只删改 `ai` 来源记录，`manual` 记录永不被自动删除。

### 6. 单一写入口 + 启动一致性检查

所有文档写入 MUST 经由 `fetcher` 中唯一的 `upsert_doc(kind, common, detail)`：在一个事务内写 `doc` + 实体表 + `discovery`。repo 刷新经由 `refresh_repo()`，同时更新 `repo.*` 与 `doc.sort_time`、`doc.last_modified_at`。

启动时（`init_db` 内，现有清僵尸任务的位置旁）检查孤儿 doc 计数并报告。**不自动删除**——孤儿意味着有 bug，静默清理会掩盖它。

### 7. `Job` + `SyncLog` 合并为 `run_log`

两者字段高度重叠（状态/计数/时间/错误），且运维看板当前需查两张表。`run_log.kind` 取 `fetch|analyze|agent`，`agent` 值为后续 agent 预留；实施中为满足「正文补全在运维视图可见」（tasks 10.4）且补全又 MUST NOT 记为采集（决策 11），增补第四个取值 `fulltext`。**代价**：`fetch` 类记录的 `total/processed` 用不上，接受。

### 8. 并发模型沿用现有 `runner.py`

保留线程池（`MAX_WORKERS=5`）、每 worker 独立 session、内存锁 + DB 状态双保险的单任务约束、取消 Event、启动清僵尸任务。**理由**：这套机制已在生产验证，且 CLAUDE.md 记录了「每个 worker 用独立 session」等踩坑结论。本次不引入 asyncio 重写。

按领域并行处理是自然需求，但**不在本次范围**——它会改变单任务锁语义，应与 agent 一并考虑。

### 9. 管道类型判定实体类型的边界

实体类型由 URL 判定而非管道类型推导。这解决 HN（rss 管道）抓回的 github 与 arxiv 链接的误判——实测共 83 个链接被改判（68 repo + 15 paper）。

**后果**：HN 抓回 github 链接时只有标题与 URL，无 stars/topics/license；抓回 arxiv 链接时无 categories/pdf_url/authors。单表设计可容忍字段空着，拆表后 `repo` / `paper` 表大量 NULL 很难看。

**决策**：这类文档 MUST 照常入库，字段留空，由独立的补全流程填充。理由：跳过会漏掉内容，而入库留空使补全的边界清晰——补全只需回答「给定 URL，补全字段」。补全流程 MUST NOT 被记为一次采集。

### 10. repo 与 paper 的采集划归另一项目

repo 与 paper 的采集与字段补全**不在本次范围**，由另立项目负责。本次的职责边界：

```
本次负责                              另立项目负责
────────────────────────────         ──────────────────────
doc / paper / repo 三张表的 schema     repo 采集（github 及其他站点）
upsert_doc 的 paper / repo 分支        paper 采集（arxiv 及其他站点）
既有 295 篇 paper + 138 个 repo 迁入    两类实体的字段补全（enrich）
领域视图混排三类实体                     GITHUB_TOKEN 等外部凭据
```

接口契约即 `upsert_doc(kind, common, detail)`：另立项目按 `paper` / `repo` 的字段结构（见决策 1）传入 `detail` 即可，无需修改 schema。

**影响**：`app/adapters/github.py` 本次不重写。库中 2 个 github 派生管道在另立项目就绪前保持停用，而非报未知类型错误——迁移时 MUST 将其 `enabled` 置 0 并记录原因。

### 11. article 正文补全：复用已丢失的抓取逻辑

`rss.py` 只解析 feed 自带内容，不抓原文，导致 1077 篇 article 正文不足 500 字（HN 占 856 篇；迁移去重后按 `word_count` 实测为 1193 篇候选）。技术/商业判定只能覆盖 43%。

**决策**：把抓取逻辑重建为独立服务 `app/services/fulltext.py`，由 RSS 正文补全与手工存入 URL 两处共用。

```
app/services/fulltext.py      抓 URL → 正文文本 + HTML + 封面 + 作者
         ↑                ↑
   article 正文补全      手工存入 URL
```

重建依据为 `archive_service.py` 的字节码（已备份），其解析逻辑包括：优先取 `<main>` / `<article>` 容器、剥除 `script` / `style` / `nav` / `aside` / `footer` / `form` / `noscript`、回退读取 OG 与 Twitter meta（`og:title` / `og:description` / `og:image` / `twitter:title` / `twitter:image` / `article:author`）、校验响应 content-type 为 HTML。

**SSRF 防护 MUST 保留**：拒绝内网与保留地址、拒绝本机地址、拒绝含用户名密码的 URL、域名解析失败即拒绝。原实现已具备这四项，重建 MUST NOT 削弱。

**为何独立于采集**：一次采集触发近千个 HTTP 请求会拖垮 fetch，且失败重试语义完全不同。补全作为独立步骤，限速分批执行。

**为何不是全部文章都抓**：仅对正文不足阈值的文章抓取。已有充足正文的（825 篇）MUST NOT 重复抓取。

**失败处理**：抓取失败 MUST NOT 阻塞入库，文档保持短正文状态；`kind_tag` 在正文不足时 MUST 留空，而非由 LLM 仅凭标题猜测。理由：留空可被后续补全流程重试，错误的判定会污染筛选结果且难以发现。

**已知会失败的站点**：机器之心已被 WAF 拦截（`sources` 中 `enabled=0`，`last_error` 有记录），36氪与虎嗅部分文章有付费墙。这些属预期失败，计入补全流程的失败统计而非视为 bug。

### 12. `source` 更名 `pipe`，`domain_id` 可空

`domain_id IS NULL` = 共享管道（有全量流，多领域共用）；有值 = 该领域的派生管道（query 本身即通道）。**为何更名**：`source` 在新模型中已不是用户订阅对象，保留旧名会持续误导；且 `pipe.domain_id` 的语义与旧 `source.domain_id`（「这个源属于哪个领域」）相反，同名不同义比改名更危险。

## Risks / Trade-offs

**[迁移损坏不可再生数据]** 1959 篇 rss 文章回溯至 2026-05-14，RSS 只给最近 N 条，损坏后无法重抓 → 迁移前 `cp` 一份 db 留底（`data/feed-curator.db.bak-<日期>`，已执行）；脚本幂等可重跑；**只读 SQLite 源库、只写 MySQL 目标库**，不原地改 `items`；写入 MySQL 后核对全部计数断言通过，再删除 SQLite 源文件——备份文件是这一步的唯一保险，删除前必须先确认备份完整。

**[孤儿 doc]** 类表继承的固有缺陷，FK 只能保证 `paper.id` 存在于 `doc`，反向管不了 → 单一写入口 + 同事务 + 启动时检查报告（决策 6）。

**[物化数据与 analysis 不一致]** `membership` / `article.kind_tag` 由 analysis 推导但独立存储 → 全部经判定写入路径维护；提供一致性核对脚本，不做自动修复。

**[URL 归一化误伤]** 剥离过多参数会把两篇不同文档误判为同一篇，且合并不可逆 → 采用白名单剥离而非全部剥离；迁移时先 dry-run 输出将被合并的分组供人工核对，确认后再执行。

**[跨站转发无法归并]** 16 篇标题相同但 URL 不同（HN 转发 → 原博客），按 `url_key` 不会合并 → 本次不处理，留给 `document_link` 的 `duplicate` 关系事后标注。这是已知缺口，非 bug。

**[三次 left join]** 领域混排视图需 left join 三张实体表 → 均为主键 join，2335 行规模无感；若将来量级上升，可为混排视图加物化表。

**[正文抓取被目标站拦截]** 机器之心已被 WAF 拦截，36氪/虎嗅部分文章有付费墙 → 抓取失败时保持短正文并留空 `kind_tag`，不阻塞入库；失败记入补全流程统计，可后续重试。

**[技术/商业筛选覆盖不全]** 正文补全成功前，`kind_tag` 对 1077 篇留空，技术/商业筛选只覆盖 43% → 这是输入质量问题而非 schema 问题；留空优于凭标题猜测，因错误判定会污染筛选结果且难以发现。补全流程跑完后覆盖率才达预期。

**[补全流程的请求量]** HN 900 篇需补全，一次性抓取会触发近千请求，可能被目标站限流或拖垮本机 → 补全独立于采集，限速分批；仅抓正文不足阈值的文档，已有充足正文的不重复抓取。

**[repo/paper 依赖外部项目]** 两类实体的采集与补全划归另立项目，在其就绪前这两类数据只有迁移进来的既有部分（295 + 137），且 HN 新抓回的 github/arxiv 链接字段长期留空 → 接口契约（`upsert_doc`）本次即固定，另立项目无需改 schema；库中 2 个 github 派生管道迁移时置为停用，避免报未知类型错误。

**[wechat 管道仍不可用]** `we-mp-rss` 容器 3 个月前 Exited(137)，502 错误源于此 → 属部署问题，不在本次范围；迁移保留该管道定义与其 26 篇既有文档。

## Migration Plan

**目标库改为本机 MySQL**（非 `db-mp` 容器，本机另有一个独立运行的 MySQL 实例）。迁移脚本从 SQLite 源库（`data/feed-curator.db`，只读）读取旧数据，在 MySQL 目标库（新建 `feed_curator` 库）写入全部 13 张新表；旧 SQLite 文件本身不被修改。

**前置**：`cp data/feed-curator.db data/feed-curator.db.bak-<日期>`；在 MySQL 实例上执行 `CREATE DATABASE feed_curator CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`。

1. **建新表**：13 张表在 MySQL 目标库全量新建。SQLite 源库不改动，不建新表——它只作为读取源。
2. **domain**：旧 `domains` 1 行「具身智能」直接搬，`keywords` 数组保留。
3. **pipe**：旧 `sources` 16 行 → 13 行。
   - `15 具身文章·36Kr` → 并入 `7 36氪`（`feed_url` 完全相同，产出 0 篇）
   - `16 具身文章·虎嗅` → 并入 `10 虎嗅`（`feed_url` 相同，产出 2 篇）
   - `12 具身论文·AI` → 并入 `6 arXiv cs.AI`（同一 category）
   - `11 具身论文·Robotics` → 转为共享管道（cs.RO，`6` 未覆盖，去掉关键词过滤）
   - `13 / 14` github → 保留为派生管道（query 不同，是真的两次请求），但 **MUST 置 `enabled=0`** 并在 `last_error` 记明「repo 采集由另立项目负责」——本次不重写 github adapter，启用会报未知类型错误
   - `3 机器之心` → 保留 `enabled=0`（被 WAF 拦截）
4. **doc + 实体表**：`items` 2335 行 → 按 `url_key` 归一为 2323 个文档。
   - dry-run 已完成（`scripts/migration/dry_run_url_dedup.py`）：11 组 23 行重复，全部为真实重复（7 组 15 行跨源完全同 URL；4 组 8 行 simonwillison 带/不带锚点）
   - `kind` 按 URL 判定，实测分布为 `article 1902 / paper 295 / repo 138`，其中 83 个来自 HN 的 github/arxiv 链接（68 repo + 15 paper，此前被误判为文章）
   - 字段分流：`description`→paper.abstract / repo.description / article.description；`content_text`→各表对应字段；`meta` JSON 按 key 拆入实体一等列
   - `sort_time` 取原 `published_at`；`repo.pushed_at` 亦取该值（旧 adapter 存的就是它）
5. **discovery**：每个原始 `items` 行一条，保留 `source_id` → `pipe_id` 映射与 `external_id`；合并管道后 `pipe_id` 指向合并目标。**实测 2331 条而非 2335**：合并后有 4 条与合并目标管道的 `(pipe_id, external_id)` 完全相同（源12 与源6 撞 2 条、源16 与源10 撞 2 条），属同管道同条目的真实重复，被唯一约束吸收。
6. **membership**：`items.domain_id=1` 的 92 行 → `membership`，`assigned_by='ai'`；按 `url_key` 合并去重后 **88 条**。
7. **analysis**：0 行。AI 从未运行，天然干净起点。
8. **reading**：1 行（唯一有 `user_rating` 的那篇）。
9. **run_log**：旧 `sync_logs` 35 行 → `kind='fetch'`；旧 `jobs` 0 行。
10. **核对**：`doc` 数 = 2323（2335 − 12 合并行）；`discovery` = 2331；`membership` = 88；三张实体表行数之和 = `doc` 数（无孤儿）。全部断言通过后才能进入下一步。
11. **切换与清理**：`.env` 设置 `DATABASE_URL` 指向本机 MySQL 目标库，代码指向新表。**核对全部通过后删除 `data/feed-curator.db`**（保留 `.bak-<日期>` 备份文件，不删）。本次是重建而非渐进迁移，不设"观察期"，MySQL 目标库里也不建旧表结构。

**回滚**：核对失败或切换后发现问题 → 用 `.bak-<日期>` 备份文件恢复 `data/feed-curator.db`，`.env` 去掉/清空 `DATABASE_URL` 使代码回退读 SQLite；`DROP DATABASE feed_curator` 后重跑迁移脚本。**一旦 SQLite 源文件被删除（步骤 11 完成后），只能靠 `.bak-<日期>` 备份回滚，不再有活的 SQLite 库可读**——这是本次选择"重建"而非"共存观察"的直接代价，故步骤 10 的核对断言必须全部通过才能执行删除。

**正文补全**：切换后先跑一轮 article 正文补全（限速分批，1193 篇候选），再执行首次判定。顺序不可颠倒——先判定会让这些文章的 `kind_tag` 留空，之后仍需重判。

**首次判定**：配置 `DEEPSEEK_API_KEY` 后跑第一次全量判定。此时那 37 篇「躺在共享管道里、标题命中具身关键词但此前进不了领域」的文档将被判入「具身智能」——这是本次重构的直接可观测收益。

## Open Questions

- **追踪参数白名单后续扩充**：当前白名单仅 `utm_*` 前缀（dry-run 证实这是现有数据中唯一跨源稳定的追踪参数）。`ref` / `spm` / `from` / `f`（36kr 的 `f=rss`，值恒定、host 特定）等在数据中出现过但剥离收益为零或不确定，未加入白名单。将来若观察到同一文档因这些参数产生假性重复，再按实际形态加入 `app/utils/url_key.py` 的 `_TRACKING_PARAM_NAMES`。
- **`paper` 是否需要子类字段**（综述/实证等）：当前只有文章需要 `kind_tag`。将来若论文也需分类，加列即可，不影响现有结构。
- **`repo` 刷新周期**：取决于实际使用中 stars 变化对判断的影响程度，运行一段时间后再定。
