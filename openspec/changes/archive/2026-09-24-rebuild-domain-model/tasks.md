## 1. 前置准备

- [x] 1.1 备份数据库到 `data/feed-curator.db.bak-<日期>`，校验备份文件大小与源文件一致（22MB）
- [x] 1.2 引入 pytest 与测试目录结构（`tests/`，`pyproject.toml` 加 dev 依赖），验证 `uv run pytest` 能跑起来并收集到 0 个测试而不报错
- [x] 1.3 建立测试用内存 SQLite fixture（独立于 `data/feed-curator.db`），验证 fixture 能建表并在测试间隔离
- [x] 1.4 创建 `.env`（从 `.env.example` 复制），`DATABASE_URL` 指向本机 MySQL 实例（`root`，非 `db-mp` 容器），`DEEPSEEK_API_KEY` 占位；在该实例上建 `feed_curator` 库（`utf8mb4`/`utf8mb4_unicode_ci`）；验证 `app/db.py` 设置 `DATABASE_URL` 时连到该 MySQL 库、取消设置时回退到本地 SQLite（回滚路径需要这条始终成立）

## 2. URL 归一化

- [x] 2.1 实现 `app/utils/url_key.py` 的归一化函数（小写 host、统一 scheme、去末尾斜杠、**剥离 fragment**、白名单剥离追踪参数），验证单元测试覆盖：协议差异、大小写、末尾斜杠、fragment 剥离、`utm_*` 剥离、身份参数保留（`id`/`v`/`p` 等）
- [x] 2.2 写 dry-run 脚本（`scripts/migration/dry_run_url_dedup.py`），对现有 2335 行 `items` 输出归一化后的重复分组。**实测结果：11 组 23 行**（原预期 7 组 16 行有误：按原始 URL 是 7 组 15 行，去 fragment 后额外发现 simonwillison.net 的 4 组 8 行——同一篇文章带/不带 `#atom-everything` 锚点，属真实重复，已获确认合并）
- [x] 2.3 依据 dry-run 结果确定追踪参数白名单最终取值：**仅 `utm_*` 前缀**。依据：363 条带 query 的 URL 中参数分布为 `utm_source/campaign/medium/content`（唯一跨源稳定重复的追踪参数，202 次出现）与一批身份参数（`id`/`v`/`p`/`t`/`page`/`postId`/`i` 等）。`ref`/`spm` 在现有数据中未出现，不预先加入白名单（见 design.md Open Questions）。36kr 的 `?f=rss`（247 次、值恒定）属 host 特定约定，不进通用白名单，已补单元测试确认其保留

## 3. 实体类型判定

- [x] 3.1 实现 `app/utils/doc_kind.py` 的 `detect_kind()` 判定函数：`arxiv.org` + 路径首段 ∈ {abs, pdf, html} → paper（pdf/html 与 abs 指向同一论文，判 article 会产生同论文两个 doc）；`github.com` 恰好两段路径 → repo，gist/issues/blob/tree 等 → article；其余 → article。13 条单元测试覆盖 arxiv abs/pdf/列表页/子域名、github 仓库根/尾斜杠/议题/文件页/tree/gist/owner 主页/首页、普通文章 URL
- [x] 3.2 对现有 2335 行跑判定并输出分布（`scripts/migration/dry_run_kind_distribution.py`）。实测：`article 1902 / repo 138 / paper 295`。HN 来源的 github 链接共 85 个——其中 68 个为仓库根路径判 repo、17 个为 issues/blob 等子路径判 article（tasks 原记录「85 个判为 repo」有误，85 是链接总数）；arxiv 链接 15 个判 paper。HN 共 83 个链接被改判（68+15），与 design.md 此前试算的 77 不符，以单元测试锁定规则的实测为准

## 4. 数据模型

- [x] 4.1 定义 `doc` 与 `paper` / `repo` / `article` 四张表（共享主键空间，实体表 `id` 同时为 PK 与 FK），验证 `create_all` 在 SQLite 与 MySQL 方言下均能建表。模型拆为 `app/models/` 包（base/doc/pipeline/analysis/ops 五文件），`url_key` 用 `String(500)`（实测最长 URL 274 字符，utf8mb4 下 2000 字节低于 InnoDB 索引上限）。已验证：DDL 双方言编译 + 真连本机 MySQL `create_all` 成功建出 13 张表
- [x] 4.2 定义 `domain` / `pipe` / `discovery` 表，验证 `pipe.domain_id` 可空且 `discovery` 上 `(pipe_id, external_id)` 唯一约束生效（同 external_id 跨管道可共存也有测试）
- [x] 4.3 定义 `analysis` / `membership` 表，验证 `membership` 主键为 `(doc_id, domain_id)` 且 analysis/membership/document_link/reading/discovery 五张引用表的外键均指向 `doc.id`（metadata 级断言）
- [x] 4.4 定义 `document_link` / `suggestion` / `run_log` 表，验证 `document_link` 的 `(from_doc_id, to_doc_id, kind)` 唯一约束生效（同对文档不同 kind 可共存也有测试）
- [x] 4.5 长文本列使用 `LongText` 变体（`article.content_text` / `content_html` / `description`、`paper.abstract` / `content_text`、`repo.readme_text`），验证在 MySQL 方言下映射为 `LONGTEXT` 而非 `TEXT`（DDL 编译断言 + 真库 information_schema 查询双重确认，6 列均为 longtext）
- [x] 4.6 JSON 列序列化统一走 `app/utils/json_str.py` 的 `json_dump()`（`ensure_ascii=False`），验证中文内容序列化不产生 `\u` 转义、往返一致

## 5. 写入路径

- [x] 5.1 实现单一写入口 `upsert_doc(kind, url, title, detail, pipe_id, external_id)`（`app/writer.py`），同一事务写 `doc` + 实体表 + `discovery`；测试覆盖新文档插入、同管道重复跳过、跨管道同 `url_key` 一份内容两条采集
- [x] 5.2 实体写入失败整体回滚（写入段 try/rollback/raise），测试模拟 flush 中途异常后三表无残留、session 干净可用
- [x] 5.3 `init_db` 启动时报告孤儿 doc 数量（不清理），测试构造孤儿后 `check_orphans` 报出正确数量
- [x] 5.4 实现 `refresh_repo()`：更新 `repo.stars/forks/open_issues/pushed_at/refreshed_at` 与 `doc.sort_time/last_modified_at`，测试确认 `analysis` 记录未被触碰

## 6. 迁移脚本

- [x] 6.1 迁移 `domains` → `domain`（1 行），`keywords` 10 项完整保留
- [x] 6.2 迁移 `sources` → `pipe`（16 → 13 行）：12→6、15→7、16→10 合并；11 转共享管道改名「arXiv cs.RO」并去关键词过滤；13/14 github 派生保留但置停用并在 `last_error` 记明原因；机器之心保持停用
- [x] 6.3 迁移 `items` → `doc` + 三张实体表：2335 行按 `url_key` 归并为 **2323** 个文档（合并 12 行），实测 kind 分布 `article 1896 / paper 290 / repo 137`，实体表之和 = doc 数
- [x] 6.4 迁移 `discovery`：**实测 2331 条而非 2335**——合并管道后 4 条与目标管道的 `(pipe_id, external_id)` 完全相同（源12 与源6 撞 2 条、源16 与源10 撞 2 条），属同管道同条目的真实重复，被唯一约束正确吸收，予以合并（design.md 已同步修正）
- [x] 6.5 迁移 `membership`：`domain_id=1` 的 92 行按 `url_key` 去重后 **88 条**，`assigned_by='ai'`
- [x] 6.6 迁移 `reading`（1 行：is_read+is_favorite+rating=4）与 `run_log`（`sync_logs` 35 行 → `kind='fetch'`），评分值未丢失
- [x] 6.7 迁移脚本幂等可重跑（先清空 13 张表再写入），连续跑两次各表计数不变
- [x] 6.8 核对脚本 `scripts/migration/verify_migration.py`：表计数、无孤儿 doc、无悬空引用、domain/pipe 形态、HN 改判抽查、评分保留，全部断言通过
- [x] 6.9 核对通过后删除 `data/feed-curator.db`（保留 `.bak-20260923`，23MB），`.env` 的 `DATABASE_URL` 已指向本机 MySQL；后续所有验证均针对 MySQL 目标库

## 7. 采集层

- [x] 7.1 `rss` / `arxiv` / `wechat` 适配器原样移植（design 非目标：不重写），字段分流抽为共享的 `app/services/doc_fields.py`；`get_adapter` 对未知类型抛错的既有行为有测试
- [x] 7.2 `manual` 管道类型落地（`app/services/manual_service.py`，单一手工管道懒创建），手工存入 github 链接判为 repo、arxiv 链接判为 paper，URL 解析出 owner/name 与 arxiv_id
- [x] 7.3 派生管道查询条件实时由所属领域关键词生成（`resolve_fetch_config`），测试覆盖「新增关键词 → 下次采集用新条件」；共享管道 config 不受影响
- [x] 7.4 `fetch_source` 改用 `upsert_doc` 并写 `run_log(kind='fetch')`；条目级失败不中断本管道、管道级异常由调度循环隔离（单管道崩溃其余照常采集，均有测试）
- [x] 7.5 HN 抓回的 github/arxiv 链接入库为 repo/paper 且字段留空——迁移即已验证（83 个改判链接全部有 doc 与实体记录、无孤儿），线上实抓亦确认

## 8. article 正文补全

- [x] 8.1 从 `archive_service` 字节码重建 `app/services/fulltext.py`（解析逐指令核对：`<article>`→`<main>`→`<body>` 容器、剥 7 类噪音标签、og/twitter meta 兜底、相对 URL join、title/description/author 截断 1000/2000/1000），离线 HTML 样本测试三类结构
- [x] 8.2 SSRF 四项防护保留（内网/保留地址、localhost、含凭据 URL、解析失败即拒），并有增强：重定向手动逐跳跟随、每跳复检公网地址
- [x] 8.3 校验响应 content-type 含 `html`，非网页（如 PDF）被拒并记 ArchiveError
- [x] 8.4 补全流程 `app/services/fulltext_backfill.py`：限速（1s/篇）分批（50 篇/批刷 run_log 进度），仅处理 `word_count<500` 且不在 3 天冷却期的文章（抓成功但仍短的 note 类文章不反复空抓）；失败保留短正文、不阻塞；记录进 `run_log(kind='fulltext')`（kind 取值在 design 决策 7 的三值基础上增补 `fulltext`，因运维看板要求补全可见）
- [x] 8.5 手工存入 URL 复用 `fulltext` 的解析与防护（同一模块函数，测试确认）
- [x] 8.6 实跑补全：迁移后正文不足的文章 1193 篇（按 word_count<500 实测，非先前按旧行数估的 1077）。共三轮：首轮因境外站点网络限流大面积失败（SSL 握手超时，同样 URL 稍后手动抓取全部成功，非代码或站点封禁问题）；网络恢复后第二轮 894 成/156 败（85%）。最终正文充足文章 **1313/1916（68.5%）**，较迁移基线 825/1902（43%）显著上升。剩余短正文构成：本轮网络失败 156 篇（可重跑推进）、抓到但本身低于 500 词的短文（冷却期内不重复抓）、付费墙/WAF 站点。机器之心仍处 WAF 拦截（管道停用中）

## 9. AI 判定

- [x] 9.1 判定契约为 `{summary, keypoints[], domains[], article_kind, stars}`（`LLMClient.analyze`），候选领域取自 `domain` 表（name + description + keywords），测试覆盖三类实体的输入组装
- [x] 9.2 输出防御：剥 markdown fence、星级越界/缺失记失败（返回 None）、丢弃未定义领域名、非法子类置空不判失败、输出无法解析记失败——五种情形均有测试
- [x] 9.3 正文不足（`word_count<500`）时 `article_kind` 强制为空，摘要/要点/归属/星级照常产出，有测试
- [x] 9.4 判定结果追加写 `analysis`（含 model 与 prompt_version），重判后旧记录保留、生效判定为最新 `status='ok'`，有测试
- [x] 9.5 物化到 `membership`（只动 ai 来源）与 `article.kind_tag`：重判收缩归属时 ai 记录被移除、manual 记录保留，analysis 追加不覆盖，有测试
- [x] 9.6 待判定筛选（`select_doc_ids`）：内容为空不判定、已有 ok 判定不自动重判、仅失败可重试、`force_all` 显式全量重判——四条各有测试
- [x] 9.7 `runner.py` 适配 `run_log(kind='analyze')`，线程池（5 worker）、单任务锁（内存+DB 双保险）、取消 Event、启动清僵尸任务全部保留；测试覆盖完整跑通、无 key 失败、运行中复用、取消后已完成保留且未开始跳过
- [x] 9.8 `Setting.categories` 路径全部移除（`scorer.py` 删除、`/api/categories` 端点与设置页不再存在）

## 10. 展示层

- [x] 10.1 领域视图（`/`）：混排三类实体按 `doc.sort_time` 倒序，支持按领域/类型/文章子类/只看收藏/按星级筛选；未判定子类的文章不出现在技术/商业筛选结果中（有测试）
- [x] 10.2 领域管理页（`/domains`）：创建（重名拒绝）/编辑描述与关键词/停用停用切换；停用领域不参与新判定（`load_domains` 只取 enabled），既有归属保留
- [x] 10.3 阅读状态操作（已读/收藏/评分/笔记/忽略，页面表单 + `/api/docs/{id}/reading`）；被忽略文档不出现在默认列表但内容与判定保留（有测试）
- [x] 10.4 运维看板（`/ops`）读 `run_log` 单表：采集历史、判定任务、正文补全同一视图可见，附文档/正文/待判定/24h 采集成败/孤儿 doc 汇总与手动操作入口
- [x] 10.5 管道页（`/pipes`）区分共享/派生展示，已有相同 feed 的共享管道时新建管道给出提示（引导走领域判定而非重复建源）；手工存入 URL 入口在本页
- [x] 10.6 `mcp_server.py` 工具适配新模型：`add_rss`/`list_pipes`/`save_url`/`list_domains`/`create_domain`/`update_domain_keywords`/`recommend_articles`/`job_status` + 微信搜索订阅（原样保留），分类类工具随 Setting 一并移除；工具层有测试

## 11. 集成验证

- [x] 11.1 端到端采集链路：应用于 :9003 启动（连 MySQL），HN/阮一峰实抓 0 新增（与迁移数据完全去重吻合）、手工存入 github URL 新建 repo doc、同一 URL 跨管道只新增 discovery 不重建 doc、无孤儿 doc
- [x] 11.2 端到端正文补全：链路经真库实跑验证（进度/成败均入 run_log、冷却生效、失败不阻塞）；补全后正文充足比例从基线 **825/1902（43%）升至 1313/1916（68.5%）**，显著上升
- [ ] 11.3 端到端判定链路：**阻塞于 `DEEPSEEK_API_KEY` 未配置**（`.env` 中为占位空值）。链路代码与单测就绪（runner 全流程有测试），配置 key 后 `POST /api/analyze/run` 或 `/ops` 页「运行 AI 判定」即可，调度器每 5 分钟也会自动起跑
- [ ] 11.4 验证可观测收益（约 37 篇文档判入「具身智能」）：依赖 11.3 的首次全量判定，key 配置后跑完即可核对（`SELECT count(*) FROM membership m JOIN doc d ON d.id=m.doc_id JOIN domain dm ON dm.id=m.domain_id WHERE dm.name='具身智能'`）
- [x] 11.5 完整测试套件 150 通过 / 1 跳过（真连 MySQL 的建表测试，无 DATABASE_URL 时跳过），`uv run pytest --cov=app` 覆盖率 **81%** ≥ 80%

## 12. 另立项目：repo 与 paper 采集（此处仅记录边界）

本次已固定接口契约 `upsert_doc(kind, common, detail)` 与 `paper` / `repo` 两张表结构，另立项目按此传入 `detail` 即可，无需修改 schema。

- [ ] 12.1 repo 采集：GitHub Search API（`query` / `min_stars` / `max_results` 配置，取 stargazers_count / forks_count / language / topics / license.spdx_id / open_issues_count / pushed_at，需 `Authorization Bearer` 与 `X-GitHub-Api-Version: 2022-11-28` 头）。参考实现的字节码在 `/tmp/feed-curator-pyc-backup/github.cpython-314.pyc`
- [ ] 12.2 paper 采集：arXiv Atom API（categories / pdf_url / authors / submitted_at）
- [ ] 12.3 两类实体的字段补全：为 HN 等聚合管道抓回的 github / arxiv 链接补全留空字段
- [ ] 12.4 配置 `GITHUB_TOKEN`（匿名限流 60 req/h → 5000）
- [ ] 12.5 就绪后启用库中 2 个 github 派生管道（迁移时已置为停用）

## 13. Agent 规划（另立 change，此处仅记录待办）

本次重构已为 agent 预留 `document_link`、`suggestion` 表与 `run_log.kind='agent'`，agent 实现为纯增量。

- [ ] 13.1 P1 领域策展 agent：每日跨文档推理（这批在讲同一件事吗、最该读哪篇、是否冒出新子主题），产出领域日报与关键词建议。依赖 `doc` / `membership` / `analysis` 就绪。优先做的理由：它用数据回答「关键词够不够」「技术/商业这刀切得对不对」，而非靠拍脑袋
- [ ] 13.2 P2 论文-仓库连接 agent：280 篇论文中 30 篇正文带 github 链接为现成种子，70 个仓库中部分明显是论文配套项目，产出 `document_link` 的 `implements` 关系
- [ ] 13.3 P3 源发现 agent：观察读/藏/忽略行为，建议新管道与调整派生管道查询。阻塞条件：`reading` 数据当前几乎为空（1 条评分、0 收藏、0 忽略），需先积累一段时间
- [ ] 13.4 P4 深读 agent（按需触发）：抓全文 → 判断信息是否充分 → 追引用 → 综合。四个 agent 中唯一真正需要多轮循环的
- [ ] 13.5 Agent 基建：`app/agents/base.py`（循环与轮数上限、工具注册）、`app/agents/tools.py`（`search_documents` / `propose_keyword` / `link_documents`）、`ai/client.py` 加 function calling 支持
- [ ] 13.6 配置 `GITHUB_TOKEN`：当前匿名限流 60 req/h，agent 反复查询仓库信息不足（配置后 5000/h）
- [ ] 13.7 硬约束落地：agent MUST NOT 直接改数据，输出写入 `suggestion` 表并由用户确认后生效。理由：能自行修改领域关键词的 agent 会悄然带偏领域定义，用户只能事后发现
