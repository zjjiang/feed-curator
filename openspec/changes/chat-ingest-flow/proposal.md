# chat-ingest-flow

## Why

微信(经 OpenClaw agent 的 MCP 调用)即将成为手工存入的主入口,而当前手工路径存出的文档不足以支撑判定:手工 repo 无 README、手工 paper 无 abstract,AI 只能基于标题打星;微信分享链接的每次分享可变参数(chksm/scene 等)会让同一篇文章重复建档、重复消耗判定。修复手工链路,才能让"转发即入库"产出与管道采集同等质量的可判定文档。

## What Changes

- 手工存入 repo 时,在同一调用内抓取 README 并填充实体;抓取失败不阻塞入库(与 github 管道的内联补全同语义)。
- 手工存入 paper(arXiv abs 链接)时,在同一调用内抓取 abstract 并填充实体;抓取失败不阻塞入库。系统此前不存在任何 paper 补全路径,此为首个。
- url_key 归一化为 `mp.weixin.qq.com` 增加 host 级特例:仅保留承载身份的 `__biz/mid/idx/sn`,剥离每分享可变的参数。全局白名单(仅 utm_*)对其他 host 保持不变。
- save_url(MCP 工具)返回值补充 title 字段,供对话场景直接回复"已存入《XX》"(实现细节,随实现落地)。
- 验证微信文章页对现有正文抓取路径的成功率(任务级;失败已有 archive_error 兜底,不阻塞本 change)。

非目标:OpenClaw 侧的 cron 推送与消息格式(零服务端改动,不在本 change);微信文章反爬的全量对抗方案;suggestion 表的 agent 建议流。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `pipeline-ingestion`:手工存入从"仅建档、字段留空待补全"升级为"建档 + 实体字段内联补全"(repo 抓 README、paper 抓 abstract),失败兜底语义与既有文章正文抓取一致。
- `document-model`:全局去重的归一化规则新增 host 级身份参数特例(微信链接),消除分享参数差异造成的重复建档。

## Impact

- 代码:`app/services/manual_service.py`(内联补全)、`app/utils/url_key.py`(host 特例)、`app/mcp_server.py`(返回 title);对应单元测试。
- 不改数据库 schema,不改判定与 web 层。README 抓取复用既有 github_client;手工存入频次低,GitHub API 无 token 限流(60/hr)内可忽略。
- 网络依赖:export.arxiv.org 间歇 SSL 失败为既有 gotcha,补全失败一律非阻塞降级,不影响入库。
