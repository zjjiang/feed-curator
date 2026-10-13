# Design: expose-intel-stats-and-repo-stars

## Context

- `_doc_dict`（`app/main.py:248`）已按 doc 组装导出字段；仓库星标在 repo 实体表（`Repo.stars`/`Repo.stars_gained`），`/docs/{id}` 详情页已用同源数据（`app/web/pages.py:176`）。
- 统计：`stats_service.collect_stats(db, start_ts, end_ts)` 与 `parse_window` 为现成服务，`/admin/stats` 页面（`app/web/pages.py:535`）即其唯一消费方；无 JSON 出口。
- 判定提示词在 `app/ai/client.py` SYSTEM_PROMPT，评级条目为「1-5 星。信息密度、原创性、实用性、深度、是否反套路。5=极有价值必读，4=值得一读…」。

## Goals / Non-Goals

- Goals：三个缺口各补一个最小出口/措辞调整；不新增表、不动判定流程。
- Non-Goals：不做统计的聚合/缓存优化；不改历史判定数据；deputy 侧消费不在本仓。

## Decisions

1. **`_doc_dict` 内按需取 repo 实体**：与 `kind_tag` 同位置处理——`doc.kind == "repo"` 时 `db.get(Repo, doc.id)`，否则 None。N+1 可接受（与现有 kind_tag/domains 同模式，列表页每页 ≤200）。
2. **`/api/stats` 薄封装**：直接 `parse_window(days, start, end)` + `collect_stats`，FastAPI Query 校验 days 枚举与日期格式，非法参数走 422。不复制统计逻辑。
3. **基线写进 SYSTEM_PROMPT 评级条目**：在「4=值得一读」后补文章基线说明，并单独点明论文/项目不适用。不引入结构化 rubric 或按 kind 拆两套 prompt——一次判定调用保持单 prompt，改动最小。
4. **测试锚定措辞而非全文**：prompt 断言只锚定关键短语（如「4 星」「基线」「论文」），避免断言全文导致措辞微调就脆断。

## Risks / Trade-offs

- [基线可能推高文章整体星数，按星级排序时文章占前] → 预期行为（用户明确要求）；历史判定不变，可对比新旧。
- [/api/stats 无鉴权] → 与 /api/docs 同面（localhost 前提，红线④），不新增暴露面。

## Migration Plan

无数据迁移。部署后重启服务生效；新字段对既有消费者透明（deputy 按可选字段渲染）。

## Open Questions

（无）
