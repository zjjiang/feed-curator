## Purpose

订阅源清单导出:生成机器可读的 docs/pipes.json(每条带内容大类),提交并 push 到 GitHub,作为导出/导入的通用交换格式;/admin 手动 + 每日自动两种触发。纯只读,失败只记 run_log。

## Requirements

### Requirement: 导出机器可读的订阅源 JSON
系统 SHALL 生成 `docs/pipes.json` 并推送:包含 version、exported_at(epoch 秒)与 pipes 数组;每条含 type、name、config(完整对象)、domain(领域名或 null)、enabled、fetch_interval_min、category(内容大类)。分类 SHALL 按有序规则(URL/名称匹配)自动判定,rss 默认归科技媒体;pipe config 中的 `export_category` 字段 MUST 作为强制覆盖(arxiv 类管道的 `category` 字段是 arXiv 分类,不参与分类覆盖)。JSON SHALL 作为导出/导入的通用交换格式。系统 MUST NOT 再生成或维护 docs/pipes.md。

#### Scenario: 论文源分类正确
- **WHEN** 存在 rss 类型的"HuggingFace 每日论文"(URL 含 daily-papers)管道
- **THEN** 该条 JSON 的 category 为"论文与研究"

#### Scenario: config export_category 覆盖规则
- **WHEN** 某管道 config 写有 `"export_category": "论文与研究"`
- **THEN** 该条 category 为"论文与研究",无论规则匹配结果

#### Scenario: JSON 结构稳定可导入
- **WHEN** 导出执行完成
- **THEN** docs/pipes.json 可被导入功能原样消费,域名字段为名字而非内部 id

### Requirement: 推送到 GitHub 仓库
系统 SHALL 在 docs/pipes.json 内容有变化时以该文件为范围 git 提交并 push;内容无变化时 MUST 跳过提交。git 操作 MUST 有超时保护,失败(记入 run_log kind='export' 的 error)不影响抓取与判定主流程。

#### Scenario: 内容变化触发提交
- **WHEN** 管道列表与上次导出相比有变化,触发导出
- **THEN** docs/pipes.json 更新并产生一个只含该文件的提交,随后 push

#### Scenario: 内容无变化跳过
- **WHEN** 生成的 JSON 与仓库内现有内容一致
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
