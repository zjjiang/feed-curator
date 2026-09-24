# Proposal: add-paper-sources

## Why

系统的论文来源目前只有 arXiv cs.AI / cs.RO 两个原始分类管道,属于"全量火龙":cs.AI 每天约 100-200 篇,与「具身智能」领域相关的比例低,噪声大;同时缺少一个社区策展层(Hugging Face Daily Papers 这类高信号精选源)。论文抓取也没有统一的出网策略(arXiv SSL 握手间歇失败,fulltext 回填同样受害),且多源接入后同一论文会以 abs/pdf/version 等不同 URL 形态重复入库——现有去重键是纯 URL 归一化,挡不住这种情况。

## What Changes

- **新增 HF Daily Papers 管道类型 `hf_papers`**:通过 hf-mirror.com 镜像调用 Hugging Face Daily Papers JSON API(国内直连可达,已实测),按日窗口抓取当日+前一日榜单;条目归一为 `https://arxiv.org/abs/{id}` 入库(自动判为论文实体);作者/摘要直填一等字段;`githubRepo` / `githubStars` / `upvotes` 存入新增的 `paper.extra` JSON 列(为后续「论文-仓库连接」agent 备料)。镜像不可达时回退代理直连 huggingface.co。
- **arXiv 适配器支持关键词派生查询**:`resolve_fetch_config` 增加 arxiv 分支——派生管道的查询由所属领域关键词实时生成(`all:"..."` OR 短语查询,仅取 ASCII 关键词,中文词过滤)。现有 cs.AI / cs.RO 共享分类管道由用户在管理后台改造为「具身智能」派生管道(运营操作,无需数据迁移)。
- **论文 URL 规范化(身份层)**:入库前 arXiv 链接统一为 `https://arxiv.org/abs/{id}`——剥版本号、归一 host(www./export.)。doc.url_key 由此在多源间天然去重(HF 给 id、HN 甩 pdf 链接、arXiv API 给带版本 abs,最终都落到同一 doc);实体字段解析仍用原始 URL,version 信息不丢失。不改 `normalize_url` 本身(保护存量 url_key)。
- **upsert 补空回填**:已存在 doc 被再次采集时,实体行空字段(如 HF 先收到的论文缺 categories,arXiv 派生管道再采到时补上)以新数据补齐,非空字段不覆盖。
- **`paper` 实体表新增 `extra` LongText JSON 列**:承载源侧信号(HF upvotes、githubRepo 等),需一次幂等 ALTER。
- **出网 HTTP 统一收口** `app/utils/outbound.py`:代理(Clash 127.0.0.1:7897,经环境变量探测)可用时代理优先、连接失败直连回退;国内可达 host(hf-mirror 等)直连优先。arxiv / rss 适配器与 fulltext 抓取迁移到该收口。

不改变:分析契约、判定流程、阅读状态、MCP 工具面、其余管道类型。

## Capabilities

### New Capabilities

- `paper-sourcing`: 论文源的订阅能力——HF Daily Papers 镜像接入、arXiv 关键词派生管道、各源的可用出口与数据形状契约、失败回退链。

### Modified Capabilities

- `document-model`: 论文身份规范化要求(同 arXiv id 跨 URL 形态归并为单一 doc)、再次采集时的补空回填要求、`paper.extra` 源侧信号列。
- `pipeline-ingestion`: 出网 HTTP 代理策略(代理优先/直连回退/国内直连),arXiv 派生查询的具体生成规则(ASCII 关键词、短语 OR)。

## Impact

- **代码**:`app/adapters/`(新增 `hf_papers.py`,改 `arxiv.py`)、`app/jobs/fetcher.py`(resolve 分支 + 入库前规范化)、`app/writer.py`(补空回填)、`app/models/doc.py`(paper.extra)、`app/utils/outbound.py`(新)、`app/services/fulltext.py` 与 `app/adapters/rss.py`(换用出网收口)、`app/web/pages.py` + `pipes.html`(管道表单支持 hf_papers 类型)。
- **数据库**:`paper` 表加一列(LongText NULL,MySQL 需幂等 ALTER,create_all 不会补列)。
- **运营**:管理后台新建 hf_papers 管道与两个 arxiv 派生管道、停用旧分类管道;`fetch_interval_min`:hf_papers 360、arxiv 派生 240。
- **成本**:判定量从纯火龙转为"关键词命中(~20-50/天)+ HF 精选(~30/天,重叠约三到五成)",DeepSeek 调用量与改造前相当或更低,信噪比显著提升。
- **风险面**:hf-mirror 为非官方镜像(格式可能变动)——回退链 + 结构校验兜底;arXiv API 括号分组语法存疑——v1 不依赖分组,只用 OR 短语。
