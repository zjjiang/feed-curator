## ADDED Requirements

### Requirement: 从 pipes.json 导入管道
/admin/pipes SHALL 提供导入入口:用户粘贴 pipes.json 内容提交后,系统 SHALL 解析并校验(接受带 pipes 键的对象或裸数组;每条 MUST type 合法、name 非空、config 为对象),对通过校验的条目按 type+name 去重后创建管道。域名字段按名字映射,领域不存在时 MUST 跳过该条而非静默降级。结果 MUST 报告导入数与跳过数及原因。导入过程 MUST NOT 修改或删除已存在的管道。

#### Scenario: 正常导入
- **WHEN** 提交包含 3 条新管道的合法 JSON
- **THEN** 创建 3 根管道,页面报告导入 3

#### Scenario: 重复跳过
- **WHEN** JSON 中某条与现有管道 type+name 相同
- **THEN** 该条跳过并计入跳过数,其余正常导入

#### Scenario: 未知领域跳过
- **WHEN** 某条 domain 指向不存在的领域
- **THEN** 该条跳过并在结果中注明,不影响其他条目

#### Scenario: 非法 JSON 拒绝
- **WHEN** 提交内容不是合法 JSON 或结构不符
- **THEN** 不创建任何管道,页面报错提示
