## ADDED Requirements

### Requirement: 手工存入的实体补全

手工存入 URL 所得文档 SHALL 在同一次存入调用内补全实体内容字段：工程项目抓取 README，论文抓取摘要。补全 MUST 在存入返回前完成，使后续判定基于完整内容而非仅标题。补全失败 MUST NOT 阻塞文档入库，文档 MUST 保留相应字段为空的状态，失败原因 MUST 可追溯。已持有内容的字段 MUST NOT 被重复抓取；同一链接被再次提交时，仍为空的字段 SHALL 重试补全。

#### Scenario: 手工存入仓库补全 README

- **WHEN** 用户手工提交一个 GitHub 仓库链接
- **THEN** 同一存入调用内抓取其 README 并填充实体
- **AND** 后续判定可基于 README 内容进行

#### Scenario: 手工存入论文补全摘要

- **WHEN** 用户手工提交一个 arXiv 论文链接
- **THEN** 同一存入调用内抓取其摘要并填充实体
- **AND** 后续判定可基于摘要内容进行

#### Scenario: 补全失败不阻塞入库

- **WHEN** 手工存入时的 README 或摘要抓取失败
- **THEN** 文档仍 MUST 完成入库，相应字段保留为空
- **AND** 失败原因 MUST 可追溯

#### Scenario: 已有内容不重复抓取

- **WHEN** 提交链接对应的文档已存在且相应字段已填充
- **THEN** 系统 MUST NOT 重复抓取该字段

#### Scenario: 字段为空时重试补全

- **WHEN** 同一链接被再次提交且相应字段仍为空
- **THEN** 系统 SHALL 重试补全该字段
