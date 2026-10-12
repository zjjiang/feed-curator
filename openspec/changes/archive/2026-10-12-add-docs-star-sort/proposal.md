## Why

deputy 军情处内嵌情报流（其 change `add-intel-stream`）需要按星级排序与门槛筛选语料；网页版订阅流已有 `sort=stars` 的现成查询（app/web/pages.py `_latest_ok_analysis_ids`），但 REST `GET /api/docs` 未暴露——排序/门槛能力只活在模板层。本 change 把这两个参数补进 REST，供 deputy 代理与未来的 API 消费方使用。

## What Changes

- `GET /api/docs` 新增可选参数：`sort`（`time`（默认，现状）| `stars`：按最新有效判定星级降序、无判定沉底）与 `min_stars`（1-5 门槛，星级 < min_stars 或未判定的文档被过滤）
- 复用 `_latest_ok_analysis_ids` 现成 join，不引入新查询模式
- 参数非法（sort 取值外 / min_stars 越界）返回 422（FastAPI 默认校验）

不改变：默认行为（不传参 = 现状 time 序）、响应结构、其他端点、MCP 工具面。

## Capabilities

### New Capabilities

- 无（skip_specs：`/api/docs` 列表端点未被任何能力 spec 覆盖，属 REST 面参数增补）

### Modified Capabilities

- 无

## Impact

- `app/main.py`（list_docs_api 两个 Query 参数 + sort 分支 join）
- 测试：`tests/` 补 list API 的 sort/min_stars 用例
- 消费方：deputy 军情处情报流（其 change 声明了对本参数的依赖与未合入降级路径）
