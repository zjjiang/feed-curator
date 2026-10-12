## Context

网页版 `_latest_ok_analysis_ids`（pages.py）已实现「最新有效判定」子查询 + 星级降序；REST 未暴露。deputy 情报流需要同名能力走 API。

## Goals / Non-Goals

补 REST 参数；不动模板、不动 MCP、不引入缓存。

## Decisions

**D1 复用 `_latest_ok_analysis_ids` 提到模块级共享**（pages.py → main.py 或抽 services），REST 与网页版同一排序语义，避免两份实现漂移。

**D2 `min_stars` 过滤语义：`stars >= min_stars`，未判定（无有效 analysis）一律被过滤**——与网页 sort=stars「无判定沉底」不同（那是排序不是过滤）；门槛即门槛，模棱两可的条目不进结果。deputy 侧「未判定条目照常呈现」的场景发生在不传 min_stars 时。

**D3 参数校验交给 FastAPI Query 约束**（sort 用 pattern、min_stars ge=1 le=5），非法即 422，不写业务校验。

## Risks / Trade-offs

- [stars join 增加查询成本] → 与网页版同款子查询，3 千行量级无感；仅 sort=stars/min_stars 请求才 join
- [deputy 在参数未合入窗口期调用] → deputy 侧已有探测降级（422 → 隐藏四星控件、退化时间序）

## Migration Plan

纯参数增补，默认行为不变。回滚 = revert。

## Open Questions

无。
