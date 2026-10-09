## Context

pipe 表有约 30 行,字段:type / name / config(JSON) / domain_id / enabled / fetch_interval_min / last_fetched_at / last_error。类型六种:rss、arxiv、github、hf_papers、wechat、manual,其中 github 大多为派生管道(domain_id 非空,查询按领域关键词生成)。部署为本机 nohup 单进程,git remote 走 SSH 已可 push,仓库 public。

## Goals / Non-Goals

**Goals:**

- 一份按平台类型分大类的 `docs/pipes.md`,自动提交推送 GitHub。
- /admin/pipes 多选批量拉取,后台执行、防重入、有 run_log 可查。

**Non-Goals:**

- 不做管道的手动分类字段(分类 = type,零 schema 改动)。
- 不做批量启停 / 批量删除 / 批量改间隔(本期只做批量拉取)。
- 不做导出历史版本管理(git 本身就是历史)。

## Decisions

1. **分类按内容大类,规则在导出服务内维护,config 可覆盖。**
   pipe.type 是接入方式不是内容类别(实际数据只有 rss/wechat/manual,论文源全是 rss),
   直接按 type 分组会把论文埋进 RSS 大类。改为按 URL/名称的有序规则匹配出内容大类:
   论文与研究 / 厂商官方 / 开发者与独立博客 / 科技媒体(rss 默认)/ 微信公众号 / 手工存入。
   pipe config 里可写 `"category"` 强制指定,规则失准时的逃生口;零 schema 变更。
   备选的手动逐源分类字段被否:要改表单、逐个设置,启发式 + 覆盖已够用。

2. **JSON 是交换格式,Markdown 是展示格式,两者同 commit 推送。**
   `docs/pipes.json`(version + exported_at + pipes 数组,domain 存名字不存 id 以便跨库)
   是导出/导入的单一格式:导出生成它,导入消费它,备份/两机迁移直接复用。
   `docs/pipes.md` 由同一份数据渲染,面向人与外部 LLM。

3. **导出 = 服务内直接 `git add/commit/push` 两个文件。**
   服务器工作区本就是仓库;subprocess 限定操作 `docs/pipes.md` 与 `docs/pipes.json`,
   `git diff --cached --quiet` 先检测无变化则跳过 commit,push 超时 30s。
   注意:工作区若有未提交的本地改动,commit 只 add 这两个文件,不碰其他。

4. **触发:手动按钮 + fetch tick 每日 due 检查。**
   复用 repo_refresh 的 `maybe_start_*` 模式(进程内单飞 + run_log 状态双保险),kind='export'。导出是纯读 + git 操作,失败不重试、不影响抓取主流程,结果在 /admin 的 run_log 里可见。

5. **导入:边界严格校验,语义不明的条目跳过并报告,绝不部分生效。**
   只接受 pipes.json 结构(dict 带 pipes 键,或裸数组);每条校验 type 合法、name 非空、
   config 为对象;type+name 重复的跳过;domain 按名字映射,名字不存在时跳过该条
   (不静默降级为共享管道,避免改变采集语义)。结果报告导入 N / 跳过 M 及原因,
   单次导入要么全部成功要么报错,不存在写一半。

6. **批量拉取照搬 README 补抓的线程模式。**
   进程内 `_BATCH_LOCK` 单飞;后台线程顺序对每个选中 pipe 调
   `fetch_source(trigger='manual')`。每个 pipe 自然落一条 kind='fetch' 的 run_log,
   不额外记总账 —— /admin 现有 run_log 流水已能看出进度。
   失败隔离:单管失败记 last_error,继续下一管。

5. **md 内容面向外部读者。**
   表头:名称 / 领域 / 状态 / 间隔 / 最近拉取 / 地址或查询。config 摘要按 type 提取(rss→feed_url,arxiv→category,github 派生→"按领域关键词生成",github 共享→query,wechat→mp_id,hf_papers→base_url,manual→"手工存入");不导出任何密钥类配置。附生成时间与统计行。

## Risks / Trade-offs

- [服务器工作区处于 feature 分支时,导出 commit 混入开发分支] → 导出只在当前分支提交,推送目标随分支;风险是脏分支名。缓解:导出前检查当前分支,非 main 时仍执行但 run_log error 字段注明分支名(或直接跳过并报错 —— 取跳过,提示需要 main)。
- [git push 在网络差时阻塞] → subprocess timeout 30s,超时记 failed,不影响任何管道功能。
- [并发导出与开发操作同时 git 操作] → 单飞锁 + 只操作两个固定文件,冲突面极小;push 被拒(non-fast-forward)时拉取 rebase 一次再试,仍失败则记 failed 等下轮。
- [批量拉取与 60s 定时抓取同管并发] → fetch_source 本身幂等(upsert),并发只会浪费一次请求;不额外加锁。
