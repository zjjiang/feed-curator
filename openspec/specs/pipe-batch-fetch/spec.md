## Purpose

批量拉取:/admin/pipes 多选管道后后台线程顺序采集,类似 yum update;单飞防重入,单管失败不中断。

## Requirements

### Requirement: 批量选择管道
/admin/pipes 页面 SHALL 为每个管道行提供复选框,并提供全选/清空控件与"更新选中"批量操作按钮。

#### Scenario: 全选后批量操作可用
- **WHEN** 用户点击全选
- **THEN** 所有管道行复选框选中,"更新选中"按钮可提交所选 id 集合

### Requirement: 后台批量拉取
提交所选管道后,系统 SHALL 在后台线程按顺序对每个管道执行 fetch_source(trigger='manual'),MUST 立即返回页面(302 回跳 + 提示),不得阻塞请求。批量任务 MUST 单飞:已有批量任务在执行时再次提交 SHALL 被拒绝并提示 busy。单管失败 MUST 记入该管 last_error 并继续下一管;每个管道的拉取结果照常落入 run_log(kind='fetch')。

#### Scenario: 批量拉取三根管道
- **WHEN** 用户勾选 3 根管道提交"更新选中"
- **THEN** 页面立即回跳并提示已开始;后台依次拉取 3 管,各产生一条 run_log

#### Scenario: 单管失败不中断
- **WHEN** 批量中第 2 根管道拉取抛错
- **THEN** 第 2 管记 last_error,第 3 管继续拉取

#### Scenario: 重入拒绝
- **WHEN** 批量任务尚在执行时再次提交
- **THEN** 返回 busy 提示,不启动第二个批量任务

#### Scenario: 空选择拒绝
- **WHEN** 未勾选任何管道提交
- **THEN** 不启动任务,回跳并提示
