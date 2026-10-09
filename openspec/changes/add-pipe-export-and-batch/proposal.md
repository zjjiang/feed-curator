## Why

订阅源(pipe)数量已近 30 个,分散在 /admin/pipes 一张平铺表里:一方面没有一份可分享的源清单(想把列表丢给 ChatGPT 做分析,本机 :9003 它访问不到),另一方面逐个点"立即拉取"效率低,需要类似 `yum update` 的批量更新。此外源的增删目前只能逐个手工录入,需要一份机器可读的 JSON 作为导出/导入的通用格式(备份、迁移两台机器分叉的数据)。

## What Changes

- 新增订阅源清单导出,按**内容大类**(论文与研究 / 厂商官方 / 开发者与独立博客 / 科技媒体 / 微信公众号 / 手工存入)生成 Markdown;分类规则在导出服务内维护,pipe config 可用 `category` 字段覆盖。(初版按 pipe.type 分组是错误假设 —— 实际数据只有 rss/wechat/manual 三种类型,论文源全部埋在 rss 里。)
- 同时导出机器可读的 `docs/pipes.json`(管道清单 + 域名 + 配置),作为导出/导入的通用交换格式。
- 两者一并提交 push 到本仓库(GitHub raw 链接公开可读);触发方式:/admin 手动按钮 + 每日自动一次,无变化时 no-op。
- /admin/pipes 增加复选框与"更新选中":后台线程顺序对选中管道执行 fetch_source,单飞防重入。
- /admin/pipes 增加导入:paste pipes.json 内容,校验、去重后创建管道,报告导入/跳过数。

## Capabilities

### New Capabilities

- `pipe-export`: 订阅源清单的内容分类、Markdown 与 JSON 生成、GitHub 推送。
- `pipe-batch-fetch`: /admin/pipes 的批量选择与后台批量拉取。
- `pipe-import`: 从 pipes.json 导入管道(校验、去重、创建)。

### Modified Capabilities

(无。)

## Impact

- 新增 `app/services/pipe_export.py`(分类规则 + md/JSON 生成 + git 推送)、`app/services/pipe_batch.py`(批量拉取)、`app/services/pipe_import.py`(导入)。
- 修改 `app/web/pages.py`、`app/web/templates/pipes.html`(复选框、批量、导入表单)、`app/web/templates/ops.html`、`app/main.py`(fetch tick 每日导出)。
- 仓库新增 `docs/pipes.md` 与 `docs/pipes.json`;源名、feed 地址等公开信息将出现在 GitHub(仓库 public,功能预期)。
- 无 schema 变更(分类覆盖复用 config JSON 列)、无新依赖。
