# pipeline-ingestion — Delta

## ADDED Requirements

### Requirement: 出网 HTTP 代理策略

系统的出网抓取(各适配器、全文抓取等)SHALL 经统一出口发起,出口按源的网络可达性选择连接顺序:

- 配置了代理时,境外源(如 arxiv.org、huggingface.co)SHALL 代理优先,连接失败(连接拒绝、超时、SSL 握手失败)SHALL 直连回退重试一次。
- 国内可达源(如 hf-mirror.com)SHALL 直连优先,失败可代理回退。
- 代理探测 MUST 快速失败(本机端口连接拒绝应立即回退),MUST NOT 显著恶化单次抓取时延。
- 代理地址 SHALL 从环境变量读取(专用变量优先,兼容常见 Clash 端口变量);未配置代理时全部直连,行为与现状兼容。

#### Scenario: 代理可用时境外源走代理

- **WHEN** 代理已配置且可用,arXiv 管道触发抓取
- **THEN** 请求经代理发出并正常返回

#### Scenario: 代理失效直连回退

- **WHEN** 代理已配置但代理进程已退出
- **THEN** 系统快速探测到连接拒绝后直连重试
- **AND** 抓取成功,直连回退不记为管道错误

#### Scenario: 国内源直连优先

- **WHEN** hf_papers 管道请求 hf-mirror.com 且代理可用
- **THEN** 请求直连发出,不经代理

#### Scenario: 未配置代理

- **WHEN** 环境中无代理配置
- **THEN** 所有出网请求直连,行为与引入本策略前一致
