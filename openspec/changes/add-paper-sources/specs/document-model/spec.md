# document-model — Delta

## ADDED Requirements

### Requirement: 论文 URL 规范化身份

系统 SHALL 在论文入库前将 arXiv 链接归一为规范身份 URL `https://arxiv.org/abs/{id}`:剥版本号,归一 host(`arxiv.org` / `www.arxiv.org` / `export.arxiv.org`),abs / pdf / html 三种路径形态归一为 abs。

- 文档去重键 MUST 基于该规范 URL:同一论文以不同 URL 形态从不同源头到达时, MUST 归并为单一文档,各管道各留一条采集溯源。
- 通用 URL 归一化规则(`normalize_url`)MUST NOT 改变——存量 url_key 的稳定性优先,规范化为论文身份层的增量规则。
- 实体字段解析 MAY 继续使用原始到达 URL(以保留 version 等信息);文档的 url 与 url_key MUST 使用规范 URL。

#### Scenario: 跨源归并

- **WHEN** HF 榜单以 id `2609.25804` 生成 `https://arxiv.org/abs/2609.25804`,随后 HN 管道抓回 `https://arxiv.org/pdf/2609.25804v2`
- **THEN** 两者归并为同一文档
- **AND** HN 管道在该文档上新增一条采集溯源,MUST NOT 创建第二个文档

#### Scenario: 带版本与不带版本归并

- **WHEN** arXiv API 返回 `https://arxiv.org/abs/2606.02578v1`,而同一论文已以无版本 URL 存在
- **THEN** 不创建新文档,仅新增采集溯源
- **AND** 论文的 version 字段按新到达的原始 URL 更新解析结果(空则补)

#### Scenario: 镜像 host 归并

- **WHEN** 条目 URL 的 host 为 `export.arxiv.org` 或 `www.arxiv.org`
- **THEN** 规范化后与 `arxiv.org` 同一论文归并

#### Scenario: 非论文 URL 不受影响

- **WHEN** 非 arXiv 的普通文章 URL 进入入库流程
- **THEN** 其去重键仍由通用 URL 归一化产生,MUST NOT 被规范化规则改动

### Requirement: 再次采集补空回填

已存在文档被再次采集时,系统 SHALL 以本次数据补齐实体行中的空字段;非空字段 MUST NOT 覆盖。补空在原有事务内完成,不产生第二次采集溯源以外的副作用。

#### Scenario: 策展层先行,召回层补全

- **WHEN** HF 榜单先收到某论文(categories 为空),随后 arXiv 派生管道带完整 categories 与 pdf_url 再次采到同一文档
- **THEN** 该文档的 categories 与 pdf_url 被补齐
- **AND** 已有的摘要、作者等非空字段保持不变

#### Scenario: 非空不覆盖

- **WHEN** 再次采集携带的字段与存量非空字段值不同
- **THEN** 存量值保持不变
- **AND** MUST NOT 触发重复判定或清除已有判定结果

#### Scenario: 无可补字段

- **WHEN** 再次采集时实体行所有可补字段均已非空
- **THEN** 仅新增采集溯源,实体行不变

### Requirement: 论文源侧信号列

paper 实体 SHALL 具有 extra JSON 列,承载源侧策展信号(如 HF upvotes、配套仓库与星数)。

- 该列内容 MUST NOT 参与实体类型判定与文档去重。
- 实体固有属性 MUST 仍按一等字段要求存储,MUST NOT 因该列存在而迁入 extra。

#### Scenario: 信号存储

- **WHEN** hf_papers 条目携带 upvotes 与 githubRepo
- **THEN** 信号以 JSON 存入该论文的 extra 列

#### Scenario: 不影响判定

- **WHEN** 论文带非空 extra
- **THEN** 其实体类型、去重身份与 AI 判定流程与不带 extra 的论文完全一致
