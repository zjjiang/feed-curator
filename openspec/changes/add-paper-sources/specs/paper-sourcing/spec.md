# paper-sourcing — Delta

## ADDED Requirements

### Requirement: HF Daily Papers 管道

系统 SHALL 提供 `hf_papers` 类型管道,抓取 Hugging Face Daily Papers 每日榜单,作为论文的策展层来源。

- 抓取窗口 SHALL 覆盖当日与前一日两个日期;同一论文在窗口间重复出现时由文档身份层去重,MUST NOT 产生重复文档。
- 默认出口 SHALL 为 hf-mirror.com 镜像的 daily_papers API;出口地址 MUST 可经管道配置替换。
- 镜像不可达或响应结构异常时,SHALL 按回退链尝试经代理直连 huggingface.co;全部出口失败时错误 MUST 记入 pipe.last_error 与 run_log,MUST NOT 影响其他管道。
- 响应中单条目解析失败 MUST 隔离:跳过该条目并计入条目级错误,不中断其余条目。

#### Scenario: 按日窗口抓取

- **WHEN** hf_papers 管道触发抓取
- **THEN** 系统 SHALL 分别请求当日与前一日的榜单
- **AND** 两日榜单中的重复论文只产生一次新文档

#### Scenario: 镜像失败回退

- **WHEN** hf-mirror.com 请求失败或返回结构不符合契约
- **THEN** 系统 SHALL 尝试经代理直连 huggingface.co 的同一 API
- **AND** 回退成功时按正常流程入库

#### Scenario: 管道失败隔离

- **WHEN** hf_papers 管道全部出口失败
- **THEN** 错误记入该管道的 last_error 与 run_log
- **AND** 同周期其他管道的抓取 MUST NOT 受影响

### Requirement: HF 条目到论文的映射

hf_papers 条目 SHALL 按以下契约映射为论文文档:

- 条目的 arXiv id SHALL 作为 external_id;文档 URL MUST 为 `https://arxiv.org/abs/{id}`(自动判为论文实体)。
- 条目标题与 summary SHALL 写入文档标题与论文摘要、正文字段。
- 作者列表 SHALL 取条目 authors 的 name 字段序列。
- 条目的 upvotes、githubRepo、githubStars SHALL 存入 `paper.extra` 源侧信号列。
- 条目 publishedAt SHALL 作为 sort_time。
- 条目不含 arXiv 学科分类 MUST 允许:categories 留空,等待再次采集补全,MUST NOT 判为失败。

#### Scenario: 标准条目映射

- **WHEN** 榜单返回 id 为 `2609.25804` 的条目,含标题、summary、作者与 upvotes
- **THEN** 系统生成 URL 为 `https://arxiv.org/abs/2609.25804` 的论文文档
- **AND** 摘要、作者、源侧信号按契约落位

#### Scenario: 缺学科分类不算失败

- **WHEN** 条目不带 arXiv categories
- **THEN** 论文的 categories 字段为空数组
- **AND** 条目正常入库,MUST NOT 记为错误

#### Scenario: 带配套仓库的条目

- **WHEN** 条目的 paper 对象带 githubRepo 与 githubStars
- **THEN** 两者与 upvotes 一并存入该论文的 extra 源侧信号
- **AND** MUST NOT 因此生成独立的工程项目文档

### Requirement: arXiv 关键词派生管道

type 为 arxiv 且绑定领域的管道,其抓取查询 SHALL 由所属领域关键词实时生成:

- 仅 ASCII 关键词参与生成,每个关键词构成为 `all:"{关键词}"` 短语,短语间以 OR 连接;非 ASCII 关键词(如中文)MUST 过滤。
- 全部关键词均为非 ASCII 时,该管道本周期 SHALL 空转(零条目),run_log 记录原因,MUST NOT 以空查询请求 arXiv。
- 结果 SHALL 按 submittedDate 倒序取前 N 条,N 可经管道配置。
- 未绑定领域的共享 arxiv 管道(按 category 抓取)行为 MUST 保持不变。

#### Scenario: 英文关键词生成查询

- **WHEN** 领域关键词含 "Humanoid Robot" 与 "vision-language-action"
- **THEN** 该管道以两短语 OR 连接的查询抓取,按投稿时间倒序返回

#### Scenario: 中文关键词被过滤

- **WHEN** 领域关键词含「具身智能」与 "Embodied AI"
- **THEN** 查询仅由 "Embodied AI" 构成
- **AND** 「具身智能」不进入查询

#### Scenario: 全中文关键词空转

- **WHEN** 某领域关键词全部为中文且存在绑定它的 arxiv 派生管道
- **THEN** 该管道不发起网络请求,run_log 记录"无可用 ASCII 关键词"

#### Scenario: 共享管道不受影响

- **WHEN** 一个未绑定领域的 arxiv 管道配置了 category
- **THEN** 其抓取仍按 category 全量拉取,与关键词机制无关

### Requirement: 论文源出口可配置

每个论文源的出口地址 MUST 可经管道配置覆盖(如镜像域名变更、新增镜像),覆盖时无须修改代码。

#### Scenario: 镜像域名变更

- **WHEN** hf-mirror.com 不可用且存在可用的新镜像
- **THEN** 用户在管道配置中替换出口地址后,后续抓取即使用新出口
