## Why

订阅源(pipe)数量已近 30 个,分散在 /admin/pipes 一张平铺表里:一方面没有一份可分享的源清单(想把列表丢给 ChatGPT 做分析,本机 :9003 它访问不到),另一方面逐个点"立即拉取"效率低,需要类似 `yum update` 的批量更新。

## What Changes

- 新增订阅源清单导出:按平台类型(rss / arxiv / github / hf_papers / wechat / manual)分大类生成 Markdown,提交并 push 到本仓库(GitHub raw 链接公开可读,供 ChatGPT 等外部工具分析)。
- 导出触发方式:/admin 手动按钮 + 每日自动一次(fetch tick 内 due 检查,复用 repo refresh 的模式);内容无变化时 push 为 no-op。
- /admin/pipes 增加复选框与"更新选中":后台线程顺序对选中管道执行 fetch_source,单飞防重入;类似 yum 的一次性批量拉取。

## Capabilities

### New Capabilities

- `pipe-export`: 订阅源清单的 Markdown 生成、分类结构与 GitHub 推送。
- `pipe-batch-fetch`: /admin/pipes 的批量选择与后台批量拉取。

### Modified Capabilities

(无 —— 现有 spec 的需求不变;run_log 新增一种 kind='export' 属于实现细节。)

## Impact

- 新增 `app/services/pipe_export.py`(生成 md + git 提交推送)、`app/services/pipe_batch.py`(批量拉取后台任务,或并入 pipe_export 同级小模块)。
- 修改 `app/web/pages.py`(导出按钮、批量拉取路由)、`app/web/templates/pipes.html`(复选框 + 批量操作条)、`app/web/templates/ops.html`(导出按钮)、fetch tick(每日导出 due 检查)。
- 仓库根新增导出文件(如 `docs/pipes.md`);数据(源名、feed URL、域名)将公开在 GitHub 上 —— 仓库已确认 public,这是本功能的预期。
- 无 schema 变更、无新依赖。
