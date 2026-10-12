## Why

add-corpus-search 的前置任务（修复判定循环从未运行）带着未完成的 1.3/1.4 被归档，其 design.md 承诺"记录后另立 change"但从未立项。本 change 补上，并把已完成的一手诊断记录在案：

- **症状**：analysis 表 0 行、run_log 从未出现 kind='analyze' 记录、`recommend_articles` 恒返回空、search_docs 的星级/领域过滤永远走"未判定"降级
- **根因（已核实）**：`.env` 的 `DEEPSEEK_API_KEY=""` 是空引号占位，`_get_llm()` 返回 None；调度门控（app/main.py `_run_analyze_cycle`）静默 return——不报错、不留 run_log，每 300 秒空转一次
- **附带发现**：membership 表有 88 行 `assigned_by='ai'` 但 analysis 0 行——物化结果失去源头（疑似迁移时代遗留），与 CLAUDE.md "membership 是 analysis 的物化"契约不符

## What Changes

- 用户提供真实 `DEEPSEEK_API_KEY` 填入 `.env`（唯一需要人工的步骤）
- 重启服务，让 analyze 周期真实运行（存量约 3191 篇，DeepSeek 成本很低）
- 验证：run_log 出现 `kind='analyze'` 记录且 analysis 表产生 `status='ok'` 行；抽查若干篇 summary/keypoints 质量
- 处理孤儿 membership：确认来源后清除或重新归属（方案在 design 定）
- CLAUDE.md 测试数量勘误（150 → 451）

不改变：判定契约（prompt/解析/防御校验）、采集管道、MCP 工具面、检索层。

## Capabilities

### New Capabilities

- 无（skip_specs：配置修复 + 运维验证，`ai-analysis` 能力的 spec 行为不变）

### Modified Capabilities

- 无

## Impact

- `.env`（key，gitignore 不入库）、可能 `app/main.py`（若把静默跳过改为记一条 run_log 失败行——是否属行为变更由 design 判断）
- 无新依赖；存量判定为一次性批量调用，成本可控
