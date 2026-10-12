# Tasks: expose-intel-stats-and-repo-stars

## 1. /api/docs 带出仓库星标（TDD）

- [x] 1.1 Red：/api/docs 对 repo 文档返回 gh_stars/gh_stars_gained、对非 repo 返回 null。跑测试确认失败。
- [x] 1.2 Green：`_doc_dict` 按 kind 取 repo 实体补两字段。全绿。

## 2. /api/stats 统计导出（TDD）

- [x] 2.1 Red：days=30 返回 daily/pipes/domains 结构且与 admin-stats 同窗口一致；start/end 自定义窗口；非法参数 422。跑测试确认失败。
- [x] 2.2 Green：新增端点，parse_window + collect_stats 直连。全绿。

## 3. 文章评级基线（TDD）

- [x] 3.1 Red：SYSTEM_PROMPT 断言含文章 4 星基线与论文/项目不适用的措辞。跑测试确认失败。
- [x] 3.2 Green：SYSTEM_PROMPT 评级条目补基线措辞。全绿。

## 4. 收尾

- [x] 4.1 全量回归 + `openspec validate` 通过；分支推远端开 PR，链接交用户审阅合并。
