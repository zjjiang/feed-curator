## 1. 判定循环排查（前置）

- [ ] 1.1 确认 DEEPSEEK_API_KEY 进入进程环境（.env 加载路径、启动日志），排除配置读取问题
- [ ] 1.2 查 run_log 中 kind='analyze' 的记录：区分「从未调度」与「调度了但全失败」，定位 runner / analyzer 的断点
- [ ] 1.3 以最小改动修复使判定循环跑通（若根因是 prompt/解析质量问题，记录证据后止损，另立 change）
- [ ] 1.4 触发一次分析运行，验证 analysis 表产生 status='ok' 行、membership 物化

## 2. 检索索引模块（TDD）

- [ ] 2.1 新增依赖 jieba、bm25s（uv add），在 Python 3.14 环境验证导入
- [ ] 2.2 先写测试：中文分词 + 索引构建 + 查询命中（in-memory SQLite 夹具造 3 类实体文档），RED → GREEN
- [ ] 2.3 实现 `app/services/search_index.py`：字段组装（title + 各实体内容预览 4000 字）、jieba 分词、bm25s 建索引、save/load（`data/search_index/`）、doc_id 顺序表
- [ ] 2.4 实现查询入口：查询词分词同构、BM25 top-K、owner/name 形态 token 保留
- [ ] 2.5 索引生命周期：写后全量重建钩子（fetcher 批后 / save_url 后 / 回填批后）、单例加载 + threading.Lock 原子替换、文件缺失自愈重建、重建失败不影响写入
- [ ] 2.6 边界测试：空查询、纯空白查询、索引缺失/损坏、长文档截断、空语料

## 3. 检索服务与片段（TDD）

- [ ] 3.1 先写测试：kind/domain/days 过滤组合、判定缺失时字段为空不排除、无命中响应、空查询报错
- [ ] 3.2 实现 `app/services/doc_search.py`：过滤查询（join 最新 ok 判定取 stars/domains）、结果组装
- [ ] 3.3 片段切取：回取 content_text、定位首个命中词、前后 ~120 字窗口、截断外命中回退正文开头
- [ ] 3.4 get_doc 服务：三类实体内容路由、max_chars 截断标注、最新成功判定附带、doc_id 不存在报错

## 4. MCP 工具接入

- [ ] 4.1 `search_docs(query, kind, domain, days, limit)` 工具：参数校验（空查询、limit 1-50、days>=1）、错误转 `{ok: false, error}`、索引不可用时提示重建
- [ ] 4.2 `get_doc(doc_id, max_chars)` 工具：同上错误语义
- [ ] 4.3 工具 docstring 按 agent 消费习惯写清参数含义与返回结构

## 5. 端到端验证

- [ ] 5.1 对真实 MySQL 跑一次索引全量重建，记录耗时与内存占用（对照设计预估 <200MB，超预期则调预览长度）
- [ ] 5.2 经 MCP 对运行中服务实测：中文主题检索、repo owner/name 检索、过滤组合、get_doc 深读、无命中反馈
- [ ] 5.3 手工 save_url 一篇新文章后立即可检索（一致性验证）
- [ ] 5.4 `uv run pytest` 全绿，`uv run pytest --cov=app` 覆盖率 ≥ 80%

## 6. 收尾

- [ ] 6.1 CLAUDE.md 目录布局与 pipeline 描述补充检索模块
- [ ] 6.2 .gitignore 确认 `data/search_index/`（派生数据不入库）
- [ ] 6.3 提交按 conventional commits 分组，推 feature 分支开 PR，等用户 review 合并
