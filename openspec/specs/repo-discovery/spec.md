## Purpose

github 管道负责从 GitHub 发现与用户领域相关的新兴热门仓库：由领域关键词与热度条件组装检索，把仓库作为工程项目实体入库，并补全 README 正文以支撑 AI 判定。

## Requirements

### Requirement: github 管道的检索发现

github 管道 SHALL 通过 GitHub 官方仓库检索接口发现仓库。

派生 github 管道的检索词 SHALL 由所属领域关键词组装：仅 ASCII 关键词参与组装，非 ASCII（中文）关键词 MUST 被排除；含空格的关键词 MUST 作为短语整体匹配；多关键词之间以「任一匹配」语义组合。组装后的检索条件 MUST 满足外部接口的长度上限，超长时按序截断且截断 MUST NOT 导致采集失败。

共享 github 管道 SHALL 使用管道配置中显式给出的检索条件，MUST NOT 受任何领域关键词影响（与 pipeline-ingestion 的共享管道语义一致）。

#### Scenario: 派生管道组装检索

- **WHEN** 「具身智能」领域的派生 github 管道执行采集
- **THEN** 检索条件由该领域关键词中的 ASCII 词条（如 "Embodied AI"、"VLA"、"Robot Learning"）以任一匹配语义组装
- **AND** 中文关键词（如「具身智能」）不出现在检索条件中

#### Scenario: 关键词变更影响检索

- **WHEN** 某领域新增 ASCII 关键词且该领域存在派生 github 管道
- **THEN** 该管道的下一次采集使用包含新词条的检索条件

#### Scenario: 检索条件超长截断

- **WHEN** 组装后的检索条件超过外部接口允许的最大长度
- **THEN** 系统截断至上限内并正常执行采集
- **AND** 截断不导致采集失败

#### Scenario: 共享 github 管道使用配置检索

- **WHEN** 存在一个共享 github 管道
- **THEN** 其检索条件完全来自管道配置
- **AND** 领域关键词变更不影响其采集行为

### Requirement: 热度窗口与星标阈值

github 管道的发现 SHALL 限定创建时间窗口与最低星标数（默认：近 90 天创建、星标 ≥ 30（实测依据见 design.md 推演六）），检索结果按星标降序取回。窗口下限 MUST 相对每次采集时刻滚动计算，窗口与阈值 MUST 可由管道配置调整。

#### Scenario: 窗口随采集时刻滚动

- **WHEN** 同一管道在两个不同日期分别采集
- **THEN** 两次检索的创建时间下限分别相对各自采集时刻计算

#### Scenario: 低星标仓库不入库

- **WHEN** 检索结果中存在星标低于阈值的仓库
- **THEN** 该仓库不进入本次采集结果

#### Scenario: 窗口与阈值可配置

- **WHEN** 管道配置指定了不同的窗口天数与星标阈值
- **THEN** 后续采集使用配置值

### Requirement: 仓库条目入库映射

检索所得仓库 SHALL 映射为工程项目实体入库：归属者、名称、星标数、fork 数、开放 issue 数、主语言、topics、license 进入 Repo 一等列；仓库主页 URL 作为去重键按全局归一化规则去重；推送时间作为该文档的跨实体排序时间。

#### Scenario: 字段完整映射

- **WHEN** 检索返回一个指标齐全的仓库
- **THEN** 各指标进入 Repo 对应一等列
- **AND** 该文档排序时间取自仓库推送时间

#### Scenario: 与其他管道去重

- **WHEN** 某 GitHub 仓库已被其他管道（如 RSS）采集入库
- **THEN** github 管道再次采集它时 MUST NOT 产生新文档
- **AND** 系统保留本管道对该仓库的采集溯源记录

### Requirement: README 补全

新入库的工程项目 SHALL 在同一采集周期内补全 README 正文并填充实体；README 补全 MUST 独立于采集（不记为一次采集，不改动采集记录）。README 抓取失败 MUST NOT 阻塞该仓库入库，实体保留 README 为空的状态，且失败 MUST 可在运维视图追溯。已持有 README 正文的仓库 MUST NOT 被重复抓取。用户 SHALL 能在运维页面对 README 为空的仓库手动触发限速分批补抓。

#### Scenario: 新仓库补全 README

- **WHEN** github 管道新采集一个仓库
- **THEN** 系统在同一采集周期内抓取其 README 并填充正文
- **AND** 该补全不产生新的采集记录

#### Scenario: README 抓取失败不阻塞

- **WHEN** 某新入库仓库的 README 抓取失败
- **THEN** 该仓库保持已入库状态且 README 为空
- **AND** 失败原因可追溯

#### Scenario: 已有 README 不重复抓取

- **WHEN** 某仓库的 README 正文已非空
- **THEN** 补全流程不再抓取该仓库

#### Scenario: 运维手动补抓

- **WHEN** 用户在运维页面触发 README 补抓
- **THEN** 系统对 README 为空的仓库限速分批补抓

### Requirement: 凭据与限流

github 采集、补全与刷新 SHALL 支持通过环境变量提供 GitHub 访问凭据；未配置凭据时系统 MUST 仍可执行仓库检索（以匿名配额运行）。所有对 GitHub 的访问 MUST 遵守其速率配额：收到限流指示时 MUST 停止或退避，MUST NOT 高频重试冲击接口。

#### Scenario: 无凭据降级采集

- **WHEN** 未配置访问凭据
- **THEN** github 管道仍按其采集间隔执行检索
- **AND** README 补全与星标刷新以受限的小批量方式运行

#### Scenario: 触发限流退避

- **WHEN** GitHub 接口返回限流指示
- **THEN** 本轮访问停止或退避
- **AND** 失败进入管道错误状态或运行记录，不影响其他管道
