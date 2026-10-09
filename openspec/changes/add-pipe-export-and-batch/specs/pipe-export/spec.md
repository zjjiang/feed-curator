## ADDED Requirements

### Requirement: 按平台类型生成订阅源清单 Markdown
系统 SHALL 生成一份 Markdown 订阅源清单,按 `pipe.type` 分大类展示(rss、arxiv、github、hf_papers、wechat、manual 六类,类名用中文平台名)。每个管道 MUST 展示:名称、所属领域(共享管道标注"共享")、启用状态、拉取间隔、最近拉取时间、地址或查询摘要(config 按 type 提取,不得包含密钥类字段)。文档 MUST 包含生成时间与源总数统计。

#### Scenario: 六类分组
- **WHEN** 存在 rss、arxiv、github 派生、hf_papers、wechat、manual 六种类型的管道
- **THEN** 文档中出现对应的六个大类,每个管道出现在其 type 对应的大类下

#### Scenario: 派生管道标注领域
- **WHEN** 某 github 管道 domain_id 非空
- **THEN** 该行领域列显示领域名,查询摘要为"按领域关键词自动生成"

#### Scenario: 不导出敏感配置
- **WHEN** 某 pipe 的 config JSON 含任何字段
- **THEN** 文档中只出现该 type 对应的摘要字段(如 rss 的 feed_url),不出现完整 config JSON

### Requirement: 推送到 GitHub 仓库
系统 SHALL 将生成的清单写入仓库内固定路径(`docs/pipes.md`),内容有变化时以该文件为范围 git 提交并 push;内容无变化时 MUST 跳过提交。git 操作 MUST 有超时保护,失败(MUST 记入 run_log kind='export' 的 error)不影响抓取与判定主流程。

#### Scenario: 内容变化触发提交
- **WHEN** 管道列表与上次导出相比有变化,触发导出
- **THEN** docs/pipes.md 更新并产生一个只含该文件的提交,随后 push

#### Scenario: 内容无变化跳过
- **WHEN** 生成的 md 与仓库内现有文件内容一致
- **THEN** 不产生提交、不 push,run_log 记录一次成功的导出

#### Scenario: 当前分支非 main 时跳过
- **WHEN** 服务器工作区不在 main 分支时触发自动导出
- **THEN** 跳过导出,run_log 记录原因,主流程不受影响

### Requirement: 导出触发方式
系统 SHALL 提供两种触发方式:/admin 页面手动按钮,以及 fetch 循环内的每日一次自动导出(到期检查,复用单飞锁与 run_log 双保险)。同一时刻 MUST 只有一个导出在执行。

#### Scenario: 手动触发
- **WHEN** 用户在 /admin 点击"导出源清单"
- **THEN** 执行生成与推送,结果通过 302 回跳 /admin 的 msg 提示

#### Scenario: 每日自动触发
- **WHEN** fetch tick 到期检查发现距上次成功导出已超过 24 小时
- **THEN** 自动执行一次导出
