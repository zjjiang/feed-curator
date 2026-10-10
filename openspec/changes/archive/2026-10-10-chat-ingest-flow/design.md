# chat-ingest-flow — Design

## Context

手工存入(`manual_service.save_url`)当前对 repo/paper 只从 URL 建档(manual_service.py:65),README 内联补全仅挂在 github 抓取周期(`repo_enrich.enrich_new_repos`),paper 摘要无任何补全路径。判定周期 300s 会先于任何补全跑到,导致手工存入的实体被"标题判定"。去重键 `url_key` 只剥 `utm_*`(url_key.py:6),微信分享链接的每次可变参数会重复建档。

可复用的既有设施:统一出网出口 `app/utils/outbound.py`(代理优先/直连回退)、arXiv id 提取 `app/utils/paper_identity.py`、arXiv Atom API 抓取与解析(`app/adapters/arxiv.py`,经 outbound)、GitHub 客户端 `app/services/github_client.py`(get_readme)、`doc_fields.build_detail` 字段路由、`upsert_doc` 的"再次写入补空回填"语义(document-model 既有 requirement)。

## Goals / Non-Goals

**Goals:**

- 手工存入 repo/paper 时,存入返回前实体内容字段已就绪(或明确失败),判定永远基于完整内容
- 微信链接归一化在同一文章上稳定,分享参数差异不产生重复文档
- save_url 的返回值足以支撑对话场景的直接回复

**Non-Goals:**

- OpenClaw 侧配置(cron 推送、消息格式)——服务端零改动
- 微信文章反爬对抗;抓取失败维持既有 archive_error 降级
- suggestion 表的 agent 建议流

## Decisions

### 决策 1:补全在 upsert 前内联完成,不走异步

`save_url` 内的顺序:kind 判定 → (repo 抓 README / paper 抓 abstract) → `build_detail` 带上内容字段 → `upsert_doc` 一次写入。失败时 detail 不带该字段、错误信息进返回值。

- 备选"入库后异步补全":被否——会在 300s 判定周期下重现"补全跑不赢判定"的竞态(github 管道为此才做成内联);MCP 工具是同步调用,调用方需要确定性结果。
- 备选"复用 enrich_new_repos(入库后按 Repo 表补)":只适用于 repo 且形态不匹配(它批量、查表);手工路径是单条、写入前,直接抓取后随 detail 一次落库更简单。
- 重试路径复用 `upsert_doc` 既有"补空回填"语义:再次提交同一链接 → 抓取仍为空的字段 → 再 upsert 即可,不新增机制。

### 决策 2:paper 摘要抓取复用 arXiv adapter 的 API 通路

从 URL 提取 arxiv_id(`paper_identity`),经 `outbound.request` 请求 export.arxiv.org API(`id_list=<id>`),复用 adapter 的 Atom 解析,取 abstract,顺带填 authors/categories/version/submitted_at——手工论文与管道论文字段对齐。

- 备选"抓 abs 页面解析 citation_abstract":被否——多一条解析路径要维护,拿不到结构化 authors/categories,且同域名网络风险无差别。
- export.arxiv.org 间歇 SSL 失败是既有 gotcha,outbound 的代理优先/直连回退即缓解;失败非阻塞降级。

### 决策 3:微信特例做成 host 级 keep-list,不动全局白名单

`url_key.py` 增加 host→保留参数 的映射(仅 `mp.weixin.qq.com` → `{__biz, mid, idx, sn}`,顺序保持),`normalize_url` 对匹配 host 应用之;其余 host 行为逐字节不变(仅 utm_*)。

- 备选"全局再加黑名单(chksm/scene/…)":被否——微信分享参数集合不可枚举,黑名单追不完;keep-list 是白名单哲学在已知 host 上的自然延伸。
- `__biz/mid/idx/sn` 四参定位一篇微信文章(biz=公众号、mid=消息、idx=多文推送序号、sn=签名);短链形态 `/s/<token>` 无 query,不受影响。
- 测试断言:参数顺序变化、携带/缺失分享参数、非微信 host 携带陌生参数均不改变既有行为。

### 决策 4:save_url 返回值补 title(纯实现层)

`manual_service.save_url` 返回 dict 增加 `title`(即写入 doc 的 title);MCP 工具原样透传。无 spec 级要求,不进 delta。

## Risks / Trade-offs

- [微信文章正文抓取成功率未知(WAF 风险)] → 存入不阻塞、archive_error 可追溯;任务里先对真实链接做一次验证,结论决定是否需要后续 change(如走 wewe-mp-rss 通道)。
- [save_url 同步时延变长(repo README/arXiv API 网络往返,超时上限约 15s)] → 对话场景可接受;失败路径即时降级返回,不拖满超时。
- [无 GITHUB_TOKEN 时 Core API 60/hr] → 手工存入低频,不构成压力;超限失败可由 /ops README 补抓兜底。
- [keep-list 若遇微信新身份参数会漏归并] → 四参是微信文章的稳定身份集;若未来出现新形态,补充映射即可,属数据问题非机制问题。

## Migration Plan

无 schema 变更、无配置变更。部署即生效;回滚 = 还原代码。存量手工建档的空字段,可用"再次提交同一链接"触发补全。

## Open Questions

(无——微信正文抓取成功率属任务期验证,不影响方案与任务拆分。)
