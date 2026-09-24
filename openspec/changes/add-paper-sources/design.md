# Design: add-paper-sources

> 2026-09-24 夜间调研 + 五轮推演。所有"实测"数据来自本机当晚探针,明早 review 时可复跑验证。

## 一、调研结论(源矩阵)

| 源 | 出口 | 本机连通性 | 数据形状 | 结论 |
|---|---|---|---|---|
| **HF Daily Papers** | `hf-mirror.com/api/daily_papers?date=`(镜像完整代理官方 API);官方域被墙 | 镜像 ✅200;官方 ❌ | JSON:条目含 `paper{id,title,summary,authors[],upvotes,githubRepo,githubStars}`、`publishedAt`;无 date 返回 50 条跨日窗口,指定 date 返回单日(实测 9-22 → 34 条) | **v1 接入(策展层)** |
| **arXiv API** | `export.arxiv.org/api/query` Atom;支持 `all:"短语"` 关键词查询 | ✅200 直连(历史上有 SSL 抖动) | 实测 `all:"humanoid robot" OR all:"vision-language-action" OR all:"embodied AI"` 命中 4429 篇,近期结果全部与具身智能高度相关 | **v1 接入(召回层,已有适配器,加派生查询)** |
| alphaXiv | `api.alphaxiv.org` 存在但 openapi.json 404,无公开文档 | ✅200 | 未知 | Deferred |
| Emergent Mind | 无 RSS(/rss、/feed 全 404),仅 S3 sitemap | ✅200 | HTML | Deferred(策展价值与 HF 重叠,讲解价值与 DeepSeek 摘要重叠) |
| themoonlight.io | 无 RSS,纯 i18n SPA,308 → /en | ✅ | HTML | Deferred(同上,且中文解读与自家摘要重叠) |
| 智源社区 BAAI | 常见 RSS/API 路径全 404,SSR 页内无显式 API | ✅200 | 未知 | Deferred |
| OpenReview | api2 403(需鉴权);会议季爆发式,非日常订阅源 | 部分 | — | Deferred |
| Semantic Scholar Graph API | 公开 API,tldr/citationCount 丰富 | ✅200 | JSON | Deferred(定位是 13.x agent 的 enrichment 工具,不是订阅源) |
| Papers with Code | 已并入 HF Papers(重定向) | — | — | 不可用 |
| RSSHub / RSS-Bridge | rsshub.app 被墙;自建容器停机中;桥接 HF 不如直连镜像 API | ❌ | — | 不采用 |
| rss.arxiv.org 分类 RSS | ✅200 | RSS | 备选出口,现不需要(API 直连正常) |

**源格局**:论文获取 = 召回层(arXiv 关键词派生,覆盖面)+ 策展层(HF Daily,社区投票筛过的子集)。两层最终都归一到 arXiv 论文身份,在文档层天然去重。这是"靠谱源"里唯二同时满足:国内可达(或可回退)、有稳定结构化出口、与现有架构(管道→适配器→URL 判型)零阻抗的。

## 二、五轮推演

### 推演 1:源格局与量级

```
             召回层                       策展层
   arXiv all:"具身智能英文关键词"        HF Daily Papers
   (派生管道,~20-60/天,估)          (~30/天,社区投票)
              \                           /
               \    归一到 arxiv abs     /
                \--------+--------------/
                         v
                 同一篇论文 = 一个 doc
                         v
              DeepSeek 判定(星级+领域)
                         v
              阅读流(按星级过滤噪声)
```

- 现状 cs.AI 火龙 100-200/天 → 派生后 20-60/天,叠加 HF ~30/天,重叠估 1/3,净增量与现状相当或更低,**信噪比显著提升**。
- 判定成本:每篇 1-2k tokens(DeepSeek),日均 50-90 篇 ≈ 忽略不计。
- 结论:双层结构成立,不需要第三层。HF 的 `githubRepo` 字段意外地为 13.2(论文-仓库连接)提供了免费种子——值得落库(推演 3)。

### 推演 2:身份与去重(多源最容易翻车的地方)

同一论文的 URL 形态:HF 给 **id**、HN 甩 **pdf 链接**、arXiv API 给**带版本 abs**、邮件/博客可能是 **export/www host** 或 **html** 形态。现有去重键 = `normalize_url`,只剥 utm/fragment/尾斜杠 → 上述形态是 4-6 个不同 url_key = 重复 doc。

三个落点选项:

| 选项 | 覆盖面 | 风险 |
|---|---|---|
| (a) 改 `normalize_url` | 全局 | **改变存量 url_key 语义**,旧 doc 的 key 不会重算,新旧两套规则并存 → 去重反而漏;白名单注释里记录了既往数据驱动决策,不该动 |
| (b) 各适配器自行规范 | 只有自家适配器 | RSS 管道承载的 arxiv 链接(HN!)漏掉——恰恰是高频重复源 |
| (c) **入库层统一规范**(`_ingest_item` 在判型后、upsert 前执行) | 所有管道类型,含 RSS 承载的第三方 arxiv 链接 | 最小侵入 |

**决策 1:选 (c)。** 规则:判型为 paper 且能解析出 arxiv id → `doc.url`/`url_key` 用 `https://arxiv.org/abs/{id}`;`build_detail` 仍吃原始 URL(保留 version 解析)。`normalize_url` 一字不动。库内存量 360 篇 paper **零重复**(已实测),纯预防性。

**决策 2:规范化条件从严。** host ∈ {arxiv.org, www.arxiv.org, export.arxiv.org} 且路径 ∈ {abs, pdf, html} 且 id 形态匹配才触发;其余 URL 一律走原路径。非论文 URL 零影响。

### 推演 3:字段完整性——策展层先到的论文永远是残缺的?

HF 先收到论文(无 categories),arXiv 派生管道当天稍晚再采到同一 doc——**现有 upsert 对已存在 doc 只补 discovery,不补字段**,残缺永久化。选项:

- (a) backfill 任务(12.2 风格,按 arxiv_id 清单批量补):多一个运维面,且重复推演 3 的问题
- (b) **upsert 补空回填**:已存在 doc 再次采集时,空字段用新数据补齐,非空不覆盖

**决策 3:选 (b)。** 自愈式:第二个到达的源天然补全第一个的残缺,与 12.2(为存量 HN 抓回链接补全)形成互补而非重复。范围 v1 限 paper 实体;非空不覆盖保证不踩手工数据;不触碰 analysis/membership(判定结果与补全正交)。

**决策 4:`paper.extra` JSON 列承载源侧信号**(upvotes/githubRepo/githubStars)。理由:13.2 需要这批免费种子;不属于实体固有属性(不违反"一等字段"要求——分类、作者等仍一等存储);不参与判型与去重。MySQL 需一次幂等 ALTER(create_all 不会给存量表加列)。

### 推演 4:网络失败推演(China network)

| 源 | 首选路径 | 失败回退 | 失败后果 |
|---|---|---|---|
| hf-mirror | 直连(国内可达,代理反而绕路) | 代理直连 huggingface.co 官方域 | pipe.last_error + run_log,周期重试 |
| arXiv API | 代理优先(Clash 7897 常驻) | 直连 | 同上;现状"间歇 SSL 失败"就此缓解 |
| fulltext 回填 | 代理优先 | 直连 | 现有可续跑机制不变,失败率下降 |
| RSS(各站) | 直连优先(多为国内 CDN)或按 host 判断 | 代理 | 不变 |

**决策 5:出网统一收口 `outbound`。** `resolve_proxy()` 从环境读代理(`OUTBOUND_PROXY` 优先,兼容 `CLASH_PROXY_PORT=7897`,再退标准 `HTTPS_PROXY`);`request()` 按 host 类别决定尝试顺序(境外 [proxy→direct],国内 [direct→proxy]),连接类错误(ConnectError/ConnectTimeout/SSL)切换路径重试一次,HTTP 状态错误不重试(与路径无关)。代理进程已死时连接拒绝毫秒级返回,不恶化时延。wechat/wewe(本地服务)不迁入。

**决策 6:hf_papers 抓取窗口 = UTC 今天/昨天/前天共三个日期。** HF 的"日"按太平洋时间换日(≈北京 15-16 点),UTC 两日窗口在北京上午会漏 PT 昨天的尾部;三日窗口数学上全覆盖(PT 今天/昨天必然 ⊆ UTC 今天-前天),多出的请求被 (pipe_id, external_id) 唯一约束天然吸收,零成本。首次运行还能白拿三天历史。

### 推演 5:运营与成本闭环

- 管道改造纯运营操作(管理后台已支持 type + domain_id 表单):新建 2 个 arxiv 派生管道(域=具身智能)、1 个 hf_papers 管道(360 分钟),停用 6/11 两个旧分类管道。**无需数据迁移**(除 extra 列 ALTER)。
- 分析侧零改动:hf_papers 条目 URL 自动判型 paper,走既有判定契约;中文摘要照常输出(prompt 本就中文)。
- 阅读侧零改动:论文混流进现有 reader,paper.extra 展示(热度角标等)另议,不进本 change。
- 失败可观测:全部失败路径落在 pipe.last_error + run_log,`/ops` 面板直接可见。

## 三、关键实现要点

- **arxiv 适配器**:`config.query` 存在 → 关键词模式(`all:"kw"` OR 连接,逐词 ASCII 过滤、剥内嵌引号、整体 URL 编码);否则 category 模式(向后兼容)。`resolve_fetch_config` 加 arxiv 分支:派生管道实时从领域关键词生成 query,与 github 分支并列。
- **hf_papers 适配器**:GET `{base}/api/daily_papers?date=YYYY-MM-DD` × 3 日期;条目校验 `paper.id` 匹配 arxiv id 形态,不匹配记条目级错误;`external_id = paper.id`;URL 构造 canonical abs;`authors[].name` → `all_authors`;`pdf_url = arxiv.org/pdf/{id}`;upvotes/githubRepo/githubStars → `meta.extra`;结构异常触发回退链。
- **fetcher**:`_ingest_item` 判型后做规范化(canonical url 传 upsert,原始 url 传 build_detail)。
- **writer**:upsert 已存在分支追加补空回填(仅 paper 字段:abstract/content_text/authors/categories/pdf_url/version/submitted_at/extra)。
- **admin**:pipes 表单 `type_supported` 与 add 分支加 `hf_papers`(config_value = 可选自定义出口,默认 hf-mirror)。
- **查询语法注意**:实测 arXiv API 括号分组行为存疑(`cat:cs.RO AND (...)` 仅命中 1),v1 **不依赖分组**,纯 OR 短语;短语本身特异度足够。括号约束 category 作为后续增强,实现期另测。

## 四、非目标(Deferred,含理由)

alphaXiv(无公开 API 文档,不硬啃)、Emergent Mind / themoonlight(无 feed;策展价值被 HF 覆盖,讲解价值被 DeepSeek 摘要覆盖)、智源社区(无公开出口)、OpenReview(API 需鉴权;会议季模式,不适合日常订阅)、Semantic Scholar(留给 13.x agent 做 enrichment:引用数/tldr)、RSSHub/RSS-Bridge(自建已停且被墙,直连镜像更简)、微信中文论文解读源(we-mp-rss 停机、机器之心 WAF)、PDF 全文深读(归 13.4 P4 深读 agent)、upvotes 前端展示(先落库)。

## 五、风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| hf-mirror 停服/改格式 | 中(非官方镜像) | 回退链(代理直连官方)+ 结构校验 + 出口可配置(spec 硬要求) |
| arXiv 关键词查询噪声偏高 | 低 | 星级过滤兜底;关键词即领域配置,改词即改查询(派生机制既有语义) |
| 派生管道因中文关键词空转 | 设计内行为 | run_log 明示原因,补英文关键词即恢复 |
| 代理未运行 | 中(用户白天关 Clash) | 毫秒级连接拒绝 → 直连回退,行为等同现状 |
| arXiv API 限流 | 极低 | 单管道每周期 1 请求,远低于 3s/次礼貌线 |
| extra 列 ALTER 失败 | 低 | 幂等脚本 + test_schema 双方言覆盖 |

## 六、实施顺序

A 出网收口(独立可先行)→ B 论文规范化 + 补空回填(身份层,所有源受益)→ C arxiv 派生查询 → D hf_papers 适配器 + extra 列 + ALTER → E admin 表单 + 运营切换 + 验收核对。D 依赖 A/B 的地基,C 独立于 D 可并行。
