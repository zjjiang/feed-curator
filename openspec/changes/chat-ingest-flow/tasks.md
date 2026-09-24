# chat-ingest-flow — Tasks

TDD:每个实现任务先写失败测试再实现。测试沿用 tests/ 既有模式(in-memory SQLite、httpx.MockTransport)。

## 1. 微信链接归一化特例(url_key)

- [ ] 1.1 先写测试:`tests/utils/test_url_key.py` 新增用例——同文章不同分享参数(chksm/scene 有无、顺序不同)归并为同一 url_key;`__biz/mid/idx/sn` 任一不同不归并;非微信 host 携带陌生参数原样保留(既有用例 MUST 全绿)。运行确认 RED
- [ ] 1.2 实现 `app/utils/url_key.py` host 级 keep-list(`mp.weixin.qq.com` → 仅保留 `__biz/mid/idx/sn`,顺序保持;其余 host 行为不变)。运行确认 GREEN

## 2. 手工存入实体补全(manual_service)

- [ ] 2.1 repo 补全测试:手工存入 github 仓库链接时,入库前抓取 README(mock github_client)并写入 `repo.readme_text`;抓取失败 → 文档仍入库、字段为空、返回 error。运行确认 RED
- [ ] 2.2 实现 repo 补全:`save_url` 内 kind==repo 时经 `github_client.get_readme` 抓取,`build_detail` 带入 `readme_text` 后 `upsert_doc`。运行确认 GREEN
- [ ] 2.3 paper 补全测试:手工存入 arXiv abs 链接时,入库前经 export.arxiv.org API(单 id)抓取(mock MockTransport)并写入 abstract/authors/categories/version/submitted_at;失败 → 降级入库。运行确认 RED
- [ ] 2.4 实现 paper 补全:`paper_identity` 提取 arxiv_id → 经 `outbound.request` 请求 Atom API(复用 adapter 解析)→ `build_detail` 带入字段。运行确认 GREEN
- [ ] 2.5 重试与去重测试:同一链接再次提交——字段已填 MUST NOT 再发抓取请求;字段为空 SHALL 重试并经 `upsert_doc` 补空回填。运行确认 RED→GREEN
- [ ] 2.6 save_url 返回值补 `title` 字段,MCP 工具透传;测试断言返回含 title

## 3. 集成验证与收尾

- [ ] 3.1 微信文章真实链接验证:取一条真实 mp.weixin.qq.com 链接走 `fulltext.fetch_and_parse`,记录成功率结论(写入任务备注或 run_log;结论若为不可用,留待后续 change,不阻塞本 change)
- [ ] 3.2 全量回归:`uv run pytest` 全绿,`uv run pytest --cov=app` 覆盖率 ≥ 80%
- [ ] 3.3 真实环境端到端:加载 .env 起服务,经 /mcp 调 save_url 存一个 GitHub 仓库与一个 arXiv 链接,确认实体字段已填充、判定基于完整内容;同一链接重复提交不重复抓取
