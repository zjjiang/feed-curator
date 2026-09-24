## Purpose

文档模型定义系统存储的三类实体——论文、工程项目、文章——及其共有身份。三类实体属性差异显著，各自完整存储；共有的身份信息统一，使归属、判定、关系、阅读状态等引用只需指向单一标识。

## Requirements

### Requirement: 三类实体各自完整

系统 SHALL 把论文、工程项目、文章作为三类独立实体存储，每类实体的特有属性 MUST 作为该实体的一等字段，MUST NOT 编码进通用的 JSON 附加字段中。

三类实体 MUST 共享统一的标识空间：任一文档在系统中有且仅有一个全局标识，该标识同时标明其实体类型。

#### Scenario: 论文实体属性

- **WHEN** 系统存储一篇论文
- **THEN** 该论文的摘要、作者列表、学科分类、PDF 地址、arXiv 标识与版本、投稿时间 MUST 作为一等字段保存

#### Scenario: 工程项目实体属性

- **WHEN** 系统存储一个工程项目
- **THEN** 该项目的拥有者、名称、star 数、fork 数、开放议题数、主语言、主题标签、许可证、最后推送时间 MUST 作为一等字段保存
- **AND** star 数与主语言 MUST 支持直接排序与筛选

#### Scenario: 文章实体属性

- **WHEN** 系统存储一篇文章
- **THEN** 该文章的作者、摘要、正文文本、正文 HTML、封面图、字数、子类 MUST 作为一等字段保存

#### Scenario: 实体特有字段不跨类污染

- **WHEN** 系统存储一篇论文或一个工程项目
- **THEN** 正文 HTML 与封面图字段 MUST NOT 存在于该实体上
- **AND** 论文的学科分类 MUST NOT 存在于文章或工程项目实体上

### Requirement: 实体类型由内容判定

实体类型 MUST 由文档自身的 URL 与内容特征判定，MUST NOT 由采集它的管道类型推导。

判定规则：URL 指向 arXiv 论文页的判为论文；URL 指向代码仓库根路径的判为工程项目；其余判为文章。

#### Scenario: 聚合站抓回的论文链接

- **WHEN** 一个 RSS 管道抓回 URL 为 `https://arxiv.org/abs/2606.04032` 的条目
- **THEN** 系统 MUST 将其判为论文实体
- **AND** MUST NOT 因来源管道为 RSS 而判为文章

#### Scenario: 聚合站抓回的仓库链接

- **WHEN** 一个 RSS 管道抓回 URL 为 `https://github.com/viggy28/streambed` 的条目
- **THEN** 系统 MUST 将其判为工程项目实体

#### Scenario: 仓库子路径不判为工程项目

- **WHEN** 抓回的 URL 指向代码仓库的议题、提交或文件页面，而非仓库根路径
- **THEN** 系统 MUST NOT 将其判为工程项目
- **AND** MUST 判为文章

#### Scenario: 桥接管道不影响判定

- **WHEN** 一个 RSS 管道实际桥接的是代码托管站的趋势榜
- **THEN** 系统按条目 URL 逐条判定实体类型
- **AND** 判定结果 MUST NOT 因管道类型统一为文章

### Requirement: 基于归一化 URL 的全局去重

系统 SHALL 以归一化后的 URL 作为文档的全局唯一键。同一篇文档经由多个管道采集时，MUST 只存储一份内容。

归一化 MUST 消除以下差异：协议差异、主机名大小写、末尾斜杠、追踪类查询参数。归一化 MUST NOT 丢弃影响文档身份的查询参数。

系统 MUST NOT 使用采集端提供的外部标识作为全局去重键——外部标识形态因管道而异（数字标识、tag URI、平台私有格式），不具备跨管道可比性。

#### Scenario: 同一论文经多个管道进入

- **WHEN** 同一篇 arXiv 论文分别被三个不同管道采集
- **THEN** 系统只存储一份该论文
- **AND** 三条采集记录 MUST 均指向这一份文档

#### Scenario: 带追踪参数的重复链接

- **WHEN** 两个管道抓回同一文章，其中一个 URL 带有追踪类查询参数
- **THEN** 归一化后两者 MUST 视为同一文档
- **AND** 系统只存储一份内容

#### Scenario: 查询参数承载身份时不归并

- **WHEN** 两个 URL 仅在承载文档身份的查询参数上不同
- **THEN** 系统 MUST 视为两篇不同文档

#### Scenario: 跨站转发不自动归并

- **WHEN** 同一篇文章的原站链接与转发站链接被分别采集，两者 URL 不同
- **THEN** 系统存储为两份文档
- **AND** 系统 MAY 通过文档关系记录两者的重复关系，但 MUST NOT 自动合并内容

### Requirement: 统一排序时间

文档身份层 MUST 提供一个可跨三类实体比较的排序时间字段。该字段 MUST NOT 被命名或表述为「发布时间」——三类实体的时间语义不同：论文为投稿时间、文章为发表时间、工程项目为最后推送时间。

各实体 MUST 同时保留其语义准确的时间字段。

#### Scenario: 混排排序

- **WHEN** 系统按时间对包含三类实体的领域视图排序
- **THEN** 排序 MUST 基于统一的排序时间字段

#### Scenario: 语义时间独立可查

- **WHEN** 用户查询某工程项目的最后推送时间
- **THEN** 系统返回该项目实体自身记录的推送时间
- **AND** 该值 MUST NOT 与排序时间字段混淆表述

### Requirement: 文档写入的完整性

创建文档 MUST 同时写入身份信息与对应实体的详细信息，两者 MUST 在同一事务中完成。系统 MUST NOT 产生只有身份记录而无对应实体记录的文档。

系统 SHALL 在启动时检查此类不一致文档并报告其数量。

#### Scenario: 实体写入失败时整体回滚

- **WHEN** 文档身份信息写入成功但实体详细信息写入失败
- **THEN** 整个写入 MUST 回滚
- **AND** 系统中 MUST NOT 残留该文档的身份记录

#### Scenario: 启动时一致性检查

- **WHEN** 系统启动
- **THEN** 系统检查是否存在无对应实体记录的身份记录
- **AND** 若存在则报告其数量

### Requirement: 工程项目的内容刷新

工程项目实体的活跃度指标 SHALL 可周期性刷新。刷新 MUST 只更新实体的内容字段与刷新时间，MUST NOT 影响该文档已有的 AI 判定结果。

#### Scenario: 刷新 star 数

- **WHEN** 系统刷新某工程项目，其 star 数由 2960 变为 5000
- **THEN** 系统更新该项目的 star 数与刷新时间
- **AND** 该文档已有的 AI 判定结果 MUST 保持不变
- **AND** MUST NOT 因内容刷新而触发重新判定

#### Scenario: 论文与文章不参与刷新

- **WHEN** 系统执行内容刷新
- **THEN** 论文与文章实体 MUST NOT 被纳入刷新范围

### Requirement: 论文 URL 规范化身份

系统 SHALL 在论文入库前将 arXiv 链接归一为规范身份 URL `https://arxiv.org/abs/{id}`：剥版本号，归一 host（`arxiv.org` / `www.arxiv.org` / `export.arxiv.org`），abs / pdf / html 三种路径形态归一为 abs。

- 文档去重键 MUST 基于该规范 URL：同一论文以不同 URL 形态从不同源头到达时， MUST 归并为单一文档，各管道各留一条采集溯源。
- 通用 URL 归一化规则（`normalize_url`）MUST NOT 改变——存量 url_key 的稳定性优先，规范化为论文身份层的增量规则。
- 实体字段解析 MAY 继续使用原始到达 URL（以保留 version 等信息）；文档的 url 与 url_key MUST 使用规范 URL。

#### Scenario: 跨源归并

- **WHEN** HF 榜单以 id `2609.25804` 生成 `https://arxiv.org/abs/2609.25804`，随后 HN 管道抓回 `https://arxiv.org/pdf/2609.25804v2`
- **THEN** 两者归并为同一文档
- **AND** HN 管道在该文档上新增一条采集溯源，MUST NOT 创建第二个文档

#### Scenario: 带版本与不带版本归并

- **WHEN** arXiv API 返回 `https://arxiv.org/abs/2606.02578v1`，而同一论文已以无版本 URL 存在
- **THEN** 不创建新文档，仅新增采集溯源
- **AND** 论文的 version 字段按新到达的原始 URL 更新解析结果（空则补）

#### Scenario: 镜像 host 归并

- **WHEN** 条目 URL 的 host 为 `export.arxiv.org` 或 `www.arxiv.org`
- **THEN** 规范化后与 `arxiv.org` 同一论文归并

#### Scenario: 非论文 URL 不受影响

- **WHEN** 非 arXiv 的普通文章 URL 进入入库流程
- **THEN** 其去重键仍由通用 URL 归一化产生，MUST NOT 被规范化规则改动

### Requirement: 再次采集补空回填

已存在文档被再次采集时，系统 SHALL 以本次数据补齐实体行中的空字段；非空字段 MUST NOT 覆盖。补空在原有事务内完成，不产生第二次采集溯源以外的副作用。

#### Scenario: 策展层先行，召回层补全

- **WHEN** HF 榜单先收到某论文（categories 为空），随后 arXiv 派生管道带完整 categories 与 pdf_url 再次采到同一文档
- **THEN** 该文档的 categories 与 pdf_url 被补齐
- **AND** 已有的摘要、作者等非空字段保持不变

#### Scenario: 非空不覆盖

- **WHEN** 再次采集携带的字段与存量非空字段值不同
- **THEN** 存量值保持不变
- **AND** MUST NOT 触发重复判定或清除已有判定结果

#### Scenario: 无可补字段

- **WHEN** 再次采集时实体行所有可补字段均已非空
- **THEN** 仅新增采集溯源，实体行不变

### Requirement: 论文源侧信号列

paper 实体 SHALL 具有 extra JSON 列，承载源侧策展信号（如 HF upvotes、配套仓库与星数）。

- 该列内容 MUST NOT 参与实体类型判定与文档去重。
- 实体固有属性 MUST 仍按一等字段要求存储，MUST NOT 因该列存在而迁入 extra。

#### Scenario: 信号存储

- **WHEN** hf_papers 条目携带 upvotes 与 githubRepo
- **THEN** 信号以 JSON 存入该论文的 extra 列

#### Scenario: 不影响判定

- **WHEN** 论文带非空 extra
- **THEN** 其实体类型、去重身份与 AI 判定流程与不带 extra 的论文完全一致
