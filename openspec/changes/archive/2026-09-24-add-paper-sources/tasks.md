# Tasks: add-paper-sources

> 实施顺序见 design.md 第六节:A 出网收口 → B 身份层 → C arxiv 派生 → D hf_papers → E 运营切换。全程 TDD(先测后写),`uv run pytest` 保持绿色,覆盖率 ≥ 80%。

## 1. A — 出网 HTTP 统一收口

- [x] 1.1 先写测试:新建 `tests/utils/test_outbound.py`——`resolve_proxy()` 的环境变量优先级(OUTBOUND_PROXY > CLASH_PROXY_PORT > 标准代理变量 > 无)、境外源 [proxy→direct] 与国内源 [direct→proxy] 的尝试顺序、连接类错误(ConnectError/ConnectTimeout)切换路径重试一次、HTTP 状态错误不重试、未配置代理时仅直连。运行确认 RED
- [x] 1.2 实现 `app/utils/outbound.py`:`resolve_proxy()`、`request(url, *, domestic=False, timeout=...)`,失败判定与回退逻辑按测试落地。运行确认 GREEN
- [x] 1.3 迁移调用点:`app/adapters/arxiv.py`、`app/adapters/rss.py`(境外/国内标记按 host 判断)、`app/services/fulltext.py` 改用统一收口;wechat/wewe 不动。验证:全量 pytest 通过,现有 MockTransport 用例改造后语义不变
- [x] 1.4 在 `.env.example`(或 CLAUDE.md 运行说明)记录 `OUTBOUND_PROXY` / `CLASH_PROXY_PORT` 的语义。验证:文档中可查

## 2. B — 论文 URL 规范化 + 补空回填(身份层)

- [x] 2.1 先写测试:`tests/utils/test_paper_identity.py`(或并入现有 url_key 测试旁)——abs/pdf/html × 带版本/不带版本 × arxiv/www/export host 全组合归并为同一 `https://arxiv.org/abs/{id}`;非 arXiv URL 原样返回;解析不出 id 的 arxiv 链接原样返回。RED
- [x] 2.2 实现 `app/utils/paper_identity.py`(canonical 函数),`app/jobs/fetcher.py:_ingest_item` 判型为 paper 后:canonical URL 传 `upsert_doc`,原始 URL 仍传 `build_detail`(保留 version 解析)。RED→GREEN
- [x] 2.3 先写测试:`tests/test_writer.py` 增补补空回填——已存在 doc 再次 upsert 时空字段(abstract/categories/pdf_url/version/submitted_at/extra)被补齐、非空字段不覆盖、无可补字段时实体行不变、补空不新增第二条 discovery 之外副作用。RED
- [x] 2.4 `app/writer.py:upsert_doc` 已存在分支实现补空回填(仅 paper 实体)。RED→GREEN
- [x] 2.5 验证多源归并集成测试:模拟 HF 条目(id 构造 canonical abs)先入库、HN 风格 pdf 链接再入库 → 单 doc 两条 discovery

## 3. C — arXiv 关键词派生管道

- [x] 3.1 先写测试:`tests/jobs/test_fetcher_config.py` 增补 arxiv 分支——派生管道实时生成 query、ASCII 关键词过滤(中文词不进查询)、全中文关键词时不发请求(适配器层收到空 query 短转)、共享 arxiv 管道配置不受影响。RED
- [x] 3.2 `resolve_fetch_config` 加 arxiv 分支;`app/adapters/arxiv.py` 支持 `config.query` 关键词模式(逐词 `all:"kw"`,剥内嵌引号,OR 连接,整体 URL 编码;`sortBy=submittedDate&sortOrder=descending`),无 query 时走原 category 模式。RED→GREEN
- [x] 3.3 适配器网络调用改走出网收口(境外标记),MockTransport 测试覆盖关键词模式请求形状。验证:pytest 通过
- [ ] 3.4 实测一次真实查询(实现后手动或集成标记测试):具身智能关键词的 query 命中量与相关性符合 design 推演 1 预期
  - ⚠️ 2026-09-24 实测受阻:arXiv API 服务端当日开始拒绝一切复合查询(OR 短语/AND/官方文档示例均 406,换出口 IP、冷却 150s 单发均复现;设计当日实测仍命中 4429)。实现代码经 MockTransport 全量验证,待上游恢复后复测(管道每 240min 自动重试,last_error 可见)

## 4. D — hf_papers 适配器 + extra 列

- [x] 4.1 先写 schema 测试:`tests/models/` 增补 `paper.extra`(LongText,双方言编译);确认 test_schema 通过后改 `app/models/doc.py` 加列。RED→GREEN
- [x] 4.2 写幂等迁移脚本 `scripts/migration/add_paper_extra.py`(information_schema 检查列存在与否,缺则 ALTER)。验证:对本地 MySQL 跑两遍,第二遍 no-op
- [x] 4.3 先写测试:`tests/adapters/test_hf_papers.py` 用真实 API 形状的离线 fixture(脱敏自 2026-09-23 实测响应)——标准条目映射(id→external_id、canonical abs URL、summary→abstract、authors[].name→all_authors、pdf_url 构造、upvotes/githubRepo/githubStars→meta.extra、publishedAt→published_at)、缺 categories 不算失败、条目 id 形态非法记条目级错误、三日期窗口调用、镜像结构异常触发回退(代理直连官方域)、全出口失败抛错给 fetcher 隔离层。RED
- [x] 4.4 实现 `app/adapters/hf_papers.py`(默认出口 hf-mirror,`config.base_url` 可覆盖;日期窗口取 UTC 今天/昨天/前天;走出网收口,国内直连优先)。RED→GREEN
- [x] 4.5 `app/adapters/__init__.py` 注册类型;`app/models/pipeline.py` type 注释补 `hf_papers`。验证:注册后 `get_adapter("hf_papers")` 可取
- [x] 4.6 admin 表单:`app/web/pages.py` 的 `type_supported` 与 `/admin/pipes/add` 分支加 hf_papers(config_value=可选自定义出口),`pipes.html` 下拉加选项。验证:pytest 中相关页面测试通过(如有)+ 手动渲染确认

## 5. E — 运营切换与验收

- [x] 5.1 对本地 MySQL 执行 4.2 的 ALTER 脚本。验证:`SHOW COLUMNS FROM paper LIKE 'extra'`
- [x] 5.2 管理后台操作:新建「具身智能」arxiv 派生管道 ×2(替代 cs.AI / cs.RO,fetch_interval_min=240)、新建 hf_papers 管道(360),手动触发各一次。验证:run_log 有成功记录,pipe.last_error 为空
  - hf_papers 成功(inserted=58);两条 arxiv 派生按用户决策「照常建,等自愈」——因 arXiv API 服务端 406(见 3.4)当前 failed,last_error 可见,每周期自动重试
- [ ] 5.3 核对多源去重:HF 与 arXiv 派生都跑过后,`SELECT arxiv_id, count(*) FROM paper GROUP BY arxiv_id HAVING count(*)>1` 仍为空;HF 收到且被派生管道再采到的论文 categories 已补全
  - 去重检查已通过(6 组接缝重复经 merge_legacy_paper_duplicates.py 并归,复跑幂等);categories 补全待 arXiv 恢复、派生管道首次成功后复核
- [x] 5.4 核对 extra 落库:带 githubRepo 的 HF 条目在 paper.extra 可查
- [x] 5.5 全量回归:`uv run pytest` 全绿、`uv run pytest --cov=app` 覆盖率 ≥ 80%
- [x] 5.6 明早向用户汇报:新管道列表、首日抓取量、与推演 1 的量级对比、hf-mirror 健康度
  - 2026-09-24 已随实施报告交付(管道列表/首跑 58 条/hf-mirror 健康);24h 稳态量级可直接看 /ops
