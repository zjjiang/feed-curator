## MODIFIED Requirements

### Requirement: 基于归一化 URL 的全局去重

系统 SHALL 以归一化后的 URL 作为文档的全局唯一键。同一篇文档经由多个管道采集时，MUST 只存储一份内容。

归一化 MUST 消除以下差异：协议差异、主机名大小写、末尾斜杠、追踪类查询参数。归一化 MUST NOT 丢弃影响文档身份的查询参数。对每分享会更换参数的平台，归一化 SHALL 按 host 应用身份参数规则：`mp.weixin.qq.com` 的链接 MUST 仅保留承载身份的 `__biz`、`mid`、`idx`、`sn` 参数，其余查询参数一律剥离；其余 host 的归一化规则 MUST 保持不变，MUST NOT 因该特例引入额外的参数剥离。

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

#### Scenario: 微信分享参数差异归并

- **WHEN** 同一篇微信文章以携带不同分享参数（如 chksm、scene）的两个链接被先后提交
- **THEN** 归一化后两者 MUST 视为同一文档
- **AND** 系统只存储一份内容

#### Scenario: 微信身份参数不同不归并

- **WHEN** 两个微信链接在 `__biz`、`mid`、`idx`、`sn` 任一参数上不同
- **THEN** 系统 MUST 视为两篇不同文档

#### Scenario: 其他 host 不受特例影响

- **WHEN** 非 `mp.weixin.qq.com` 的链接携带白名单之外的查询参数
- **THEN** 该参数 MUST 原样保留，归一化行为 MUST 保持既有规则
