## Context

OpenClaw 将经 MCP 以自然语言问题检索 feed-curator 语料。语料约 3 千篇（article 2415 / paper 462 / repo 180），内容覆盖率近 100%，article 正文均长约 7000 字（最长 60 万字）。部署约束：原生单进程运行（无 docker，ghcr/docker.io 不可达）、Python 3.14、MySQL 主存储、中国网络环境。调研结论（2026-10-10）：进程内方案适配度最高，独立服务（MeiliSearch 等）与 RAG 平台（Dify/RAGFlow 等）在量级与部署形态上错配。

另一个输入：`analysis` 表为 0 行——判定循环从未成功运行过，DEEPSEEK_API_KEY 已配置。检索不依赖判定层，但星级/领域过滤与 recommend 工具依赖它，需一并排查。

## Goals / Non-Goals

**Goals:**

- agent 能用中文关键词检索语料，取回片段与元信息
- agent 能按 doc_id 深读全文（RAG 消费）
- 索引与 MySQL 写入保持一致，且索引可随时全量重建
- 判定循环恢复运行，星级/领域过滤有数据可用

**Non-Goals:**

- 语义向量检索与混合排序（二期：bge-m3 via SiliconFlow + 暴力余弦 + RRF，留独立 change）
- 判定质量复盘、web 界面搜索框、OpenClaw 会话记忆（OpenViking 另议）

## Decisions

**D1. 分词与排序：jieba + bm25s（进程内）**

- jieba（⭐35k，中文分词事实标准）做索引与查询的同构分词；bm25s（纯 Python，numpy/scipy 稀疏矩阵实现）做 BM25 排序。
- 备选否决：MySQL ngram FULLTEXT（bigram 噪音大，且排序质量不如 BM25 正统）；MeiliSearch（单二进制可行但多一个常驻服务 + 同步管道，3k 语料撑不起）；tantivy-py（cp314 wheel 有但无中文分词器，Python 侧只能 ngram）；Elasticsearch/Qdrant/Milvus/Dify/RAGFlow（分布式或平台级，部署形态与本项目的原生单进程约束冲突，docker 不可达）。
- bm25s 星数低（1.8k）但算法为教科书级、依赖仅 numpy/scipy，维护风险可忽略；3k 文档建索引亚秒级，每次写入后全量重建的开销可接受。

**D2. 索引内容与字段**

| 实体 | 索引字段 |
|------|---------|
| 全部 | doc.title |
| article | content_text 前 4000 字（预览截断，覆盖绝大多数文章的判定相关内容，控制索引体积与内存） |
| paper | abstract + title（arxiv_id、authors 不参与词法检索） |
| repo | name/owner 拼接 + description + readme_text 前 4000 字 |

- 检索词同时匹配 owner/name（repo 的 `owner/name` 形态查询按原样保留为一个 token 处理，避免被 jieba 切碎）。
- 结果片段（snippet）不做索引内高亮：命中后回 MySQL 取 content_text，Python 侧定位首个查询词位置、切前后文各 ~120 字窗口。查询词在预览截断之外命中时，片段回退到正文开头。

**D3. 索引生命周期：写后全量重建**

- 索引文件落盘 `data/search_index/`（bm25s 的 save 格式 + doc_id 顺序表），属派生数据，可随时删除重建，不入 MySQL。
- 触发点：fetcher 一次 fetch_source 批量写入完成后重建一次（不逐文档重建）；manual_service.save_url 完成后重建；fulltext_backfill / repo README 回填批次完成后重建。重建耗时秒级，且都在请求/任务尾部异步感不强的位置。
- 进程内持有已加载索引单例；首次检索时若文件缺失则自动重建（自愈）。锁：重建期间检索返回旧索引（原子替换加载），用简单 threading.Lock 保护加载/替换。
- 不引入增量更新：3k 语料下全量重建的一致性故事最简单，增量倒排删除是复杂度陷阱。

**D4. MCP 工具签名**

```
search_docs(query, kind=None, domain=None, days=None, limit=10)
  -> {ok, count, results: [{doc_id, kind, title, url, snippet, stars, domains, sort_time}], note?}
  limit 上限 50；days 按 sort_time 窗口；domain 按 membership(ai+manual) 精确匹配领域名

get_doc(doc_id, max_chars=8000)
  -> {ok, doc_id, kind, title, url, content, content_truncated, content_total_chars,
      analysis: {summary, keypoints, domains, stars} | None}
  内容超 max_chars 截断并标注总长；analysis 取最新 status='ok' 行
```

- 两个工具均不鉴权（与既有 MCP 工具一致，本地自用）。
- 错误语义沿用工具边界惯例：参数错误返回 `{ok: false, error}`；索引不可用返回失败并提示重建，不抛异常。

**D5. 判定循环排查（前置任务，不预设结论）**

按嫌疑度排查：`DEEPSEEK_API_KEY` 是否真的进了进程环境（.env 加载路径）→ analyze 周期是否有 run_log 记录（区分"从未调度"与"调度了但全失败"）→ runner 僵尸清理逻辑是否误吞。修复以最小改动为准；若涉及判定质量本身（prompt、模型返回解析），超出本 change 范围，记录后另立。

## Risks / Trade-offs

- **内存占用**：3k × 4000 字预览 + bm25s 稀疏矩阵预估 < 200MB；若实测超预期，降预览长度或改为 title+首段索引（Trade-off：召回率下降）。
- **词法检索的语义鸿沟**：换词问法会漏召。缓解：OpenClaw 是多轮 agent，可自行改写关键词；二期 embedding 补齐。
- **jieba 首次加载**：词典加载约 1s，发生在首次索引构建（进程启动后首次写入或首次检索），一次性成本，可接受。
- **Python 3.14 兼容**：jieba / bm25s 均纯 Python，无二进制 wheel 依赖；实施时在 uv 环境实测导入。
- **判定排查可能扩散**：若根因是 prompt/解析质量问题，按 D5 边界止损，不在本 change 内修判定质量。
