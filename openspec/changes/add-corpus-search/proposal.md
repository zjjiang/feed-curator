## Why

OpenClaw（微信对话侧的 agent）即将以 feed-curator 的 MCP 工具为主要信息入口：根据用户在对话里提出的问题，检索本地语料并回答。当前 MCP 面向 agent 的工具只有推荐（recommend_articles，依赖判定星级）与管道管理，**没有任何检索能力**——无法按主题、关键词查找文档，也无法读取指定文档全文。语料现状（2026-10-10）：约 3 千篇文档，article 正文 / paper 摘要 / repo README 覆盖接近 100%，检索的原料充足，缺的只是检索层。

## What Changes

- 新增进程内中文词法检索：对 doc 标题 + 实体内容（article 正文预览 / paper 摘要 / repo README 与描述）建立倒排索引，中文分词后按 BM25 排序。选型调研结论（2026-10-10）：jieba 分词 + bm25s 排序，纯 Python、零常驻服务、3k 语料全量重建秒级；语义向量检索（bge-m3 + 余弦 + RRF 融合）列为二期非目标。
- MCP 新增 `search_docs` 工具：按查询词检索，支持实体类型、领域、时间窗口过滤，返回 doc_id / 标题 / URL / 内容片段 / 星级 / 领域，供 agent 多轮迭代查询。
- MCP 新增 `get_doc` 工具：按 doc_id 返回文档全文与最新判定摘要，供 agent 对候选结果深读（RAG 式消费）。
- 索引随写入保持最新：文档写入（管道采集、手工存入）后全量重建索引（秒级），不引入增量同步机制。
- 排查并修复判定循环从未运行的问题（analysis 表 0 行，DEEPSEEK_API_KEY 已配置但无任何成功判定）——search_docs 的星级/领域过滤与 recommend 工具都依赖判定层。

非目标：语义向量检索与混合排序（二期独立 change）；判定质量的复盘调优；OpenClaw 侧的会话记忆（评估中的候选项为 OpenViking，另行立项）；web 阅读界面的搜索框。

## Capabilities

### New Capabilities

- `doc-search`: 面向 agent 的语料检索——关键词检索、结果片段、全文读取、索引与写入的一致性。

### Modified Capabilities

（无——检索是新增读路径，不改变既有管道采集、手工存入与判定行为。）

## Impact

- 代码：新增 `app/services/search_index.py`（分词 + 索引 + 检索，进程内）与 `app/services/doc_search.py`（检索服务：过滤、片段切取、结果组装）；`app/mcp_server.py` 新增 `search_docs` / `get_doc` 两个工具；`app/writer.py` 或 fetcher 循环末尾触发索引重建；依赖新增 `jieba`、`bm25s`（均为纯 Python，PyPI 可达）。
- 存储：索引文件落盘 `data/search_index/`（可随时重建的派生数据，不入 MySQL，不加 schema）。
- 判定层排查涉及 `app/jobs/runner.py`、`app/ai/analyzer.py`、`.env` 配置读取路径；预期为配置或调度问题的小修复。
- 网络：无新增外部依赖（embedding API 留二期）；jieba 词典随包分发，首建索引无网络请求。
- 风险：3k 长文档（最长 60 万字）全量重建的内存占用需实测；article 正文预览截断长度（设计定为 4000 字）需在索引质量与体积间验证。
