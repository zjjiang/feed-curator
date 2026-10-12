## Context

判定循环的调度门控写法是 `if not _get_llm(): return`——key 缺失时静默空转。诊断由双仓一致性体检（2026-10-12）完成并实证（本地 MySQL 只读查询：analysis_total=0、runlog 无 analyze、.env key 为空引号占位、服务经 launchd 在跑）。

## Goals / Non-Goals

**Goals**：判定循环真实跑通一轮全量；产出可验证（run_log + analysis 行）；孤儿 membership 有处理结论。
**Non-Goals**：判定质量调优（prompt 迭代另立）；向量检索；deputy 侧任何改动。

## Decisions

**D1 修复 = 填真实 key + 重启，不改调度代码**。门控语义本身合理（无 key 就别跑）；真正缺陷是**静默**——空转连一行日志都不留，导致此问题潜伏数月。备选"无 key 也记一条 run_log(error)"属行为增强，超出本 change，若要做另立（记入 Open Questions 候选）。

**D2 验证标准写死**：`run_log` 出现 `kind='analyze'` 且 `status` 有记录；`analysis` 表出现 `status='ok'` 行；抽查 5 篇的 summary/keypoints 人类可读且与原文相符。达标即勾 1.3/1.4 的遗愿。

**D3 孤儿 membership 先查后清**：88 行 `assigned_by='ai'` 对应 0 analysis——先按 doc 抽样确认这些归属是否合理（合理→改为 `assigned_by='manual'` 留存；不合理→删除）。不武断清空。

## Risks / Trade-offs

- [3191 篇全量判定的 API 成本与时长] → DeepSeek 单价极低；analyze 周期自带限速/断点（沿用现有 runner）
- [key 泄漏] → key 只进 `.env`（gitignore），不进任何文档/提交
- [判定质量差污染 search_docs 过滤] → spec 已定义"未判定不排除"降级，坏判定最坏等同未判定

## Migration Plan

填 key → 重启（launchd）→ 周期自动消化存量 → 验证。回滚 = 清空 key（循环回到静默跳过，无数据损坏）。

## Open Questions

- 孤儿 membership 的抽样结论（D3 第一步做完才能定清理方案）
- 是否值得给"静默跳过"加一条失败 run_log（增强，另立与否待定）
