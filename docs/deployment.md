# feed-curator 部署架构

本文档描述 feed-curator 及其依赖的部署架构、现状痛点，以及容器化改造方案。

> 适用范围：本仓库（feed-curator）。we-mp-rss、RSSHub 为独立项目，本文仅描述与它们的集成关系。

## 服务全景

| 服务 | 端口 | 角色 | 现状运行方式 |
|------|------|------|-------------|
| feed-curator | 9003 | 采集 + AI 评分 + Web + MCP | 本机 uv + nohup |
| we-mp-rss | 9001 | 微信公众号采集 | 本机 uv + nohup |
| RSSHub | 9002 | RSS 桥接（虎嗅等） | Docker (restart=always) |
| Homebrew MySQL | 3306 (IPv4) | feed-curator 的库 `feed_curator` | brew services |
| db-mp (Docker MySQL) | 3306 (IPv6) | we-mp-rss 的库 `we_mp_rss` | Docker (restart=always) |
| redis | 6379 | we-mp-rss 任务队列 | Docker |
| singbox | - | we-mp-rss 抓公众号的代理 | Docker |

## 现状架构

```
                          ┌─────────────────────────────────────────┐
                          │              macOS 宿主机                  │
  浏览器/小龙虾 ──:9003──► │  ┌──────────────┐                         │
                          │  │ feed-curator │  本机 uv + nohup         │
                          │  │   (FastAPI)  │  重启后需手动拉起 ⚠       │
                          │  └──────┬───────┘                         │
              ┌───────────┼─────────┼──────────────┬──────────────┐   │
              ▼           │         ▼              ▼              ▼   │
     ┌─────────────────┐  │  ┌─────────────┐ ┌──────────┐  ┌─────────┐│
     │ Homebrew MySQL  │  │  │  we-mp-rss  │ │  RSSHub  │  │ redis   ││
     │ IPv4 127.0.0.1  │◄─┘  │   :9001     │ │  :9002   │  │ :6379   ││
     │   :3306 ⚠       │     │ 本机uv+nohup │ │ (Docker) │  └─────────┘│
     │ db=feed_curator │     └──────┬──────┘ └──────────┘             │
     └─────────────────┘            │ DB 走 [::1]:3306                │
            ▲ 抢 3306 冲突           ▼                                 │
     ┌──────┴──────────┐   ┌─────────────────┐  ┌──────────┐          │
     │  db-mp (Docker) │   │  db-mp 同一实例  │  │ singbox  │ 代理      │
     │ IPv6 *:3306 ⚠   │   │  db=we_mp_rss   │  └──────────┘          │
     │ restart=always  │   └─────────────────┘                       │
     └─────────────────┘                                             │
                          └─────────────────────────────────────────┘
```

### 现状痛点

1. **3306 端口冲突**：Homebrew MySQL（IPv4 `127.0.0.1:3306`）与 db-mp（IPv6 `*:3306`）
   抢同一端口，连谁取决于 IPv4/IPv6 解析。用 `127.0.0.1` 连到 Homebrew，用 `[::1]` 连到
   db-mp，极易连错库。
2. **重启全靠手动**：feed-curator、we-mp-rss 都是 `nohup`，机器重启后服务全部丢失。
3. **启动姿势是隐性知识**：we-mp-rss 必须 `PORT=9001` + `DB=mysql+pymysql://...@[::1]:3306/...`
   才能起，无文档，每次靠摸索。
4. **配置散落**：DB 里微信源 / RSS 源写死了 `localhost:9001` / `localhost:9002`。

## 目标架构（容器化 feed-curator，保留本机 MySQL）

决策：仅容器化 feed-curator；数据库继续用本机 Homebrew MySQL；we-mp-rss / RSSHub 维持现状。

```
                          ┌──────────────────────────────────────────┐
                          │               macOS 宿主机                 │
  浏览器/小龙虾 ──:9003──► │  ┌────────────────────────────────────┐   │
                          │  │  docker network: feed-net           │   │
                          │  │  ┌──────────────┐  restart=unless-  │   │
                          │  │  │ feed-curator │  stopped ✓        │   │
                          │  │  │  容器 :9003   │  healthcheck ✓    │   │
                          │  │  └──────┬───────┘                   │   │
                          │  │         │ 走容器内网名字直连          │   │
                          │  │         ▼                           │   │
                          │  │  ┌─────────────┐  (db-mp 加入        │   │
                          │  │  │   db-mp     │   feed-net)         │   │
                          │  │  │  MySQL 8.3  │  restart=always ✓   │   │
                          │  │  │ feed_curator│                     │   │
                          │  │  │ + we_mp_rss │  一个实例两个库      │   │
                          │  │  └─────────────┘                     │   │
                          │  └────────────────────────────────────┘   │
                          │                                            │
                          │   we-mp-rss(:9001) → db-mp(同实例)          │
                          │   RSSHub(:9002) 维持现状                    │
                          │                                            │
                          │   ✗ Homebrew MySQL 已退役                   │
                          └──────────────────────────────────────────┘
```

### 关键改动（合并到 db-mp）

数据库统一到 db-mp 这一个 MySQL 实例：`feed_curator` 与 `we_mp_rss` 两个库共存。
feed-curator 容器与 db-mp 同在 `feed-net` 网络，容器间用服务名 `db-mp` 直连，
**不再经宿主机端口、不再有 IPv4/IPv6 之分**，3306 冲突从根上消失。

1. **迁移数据**：把 Homebrew MySQL 的 `feed_curator` 库（42.8MB，约 3968 篇）
   `mysqldump` 导出 → 导入 db-mp。迁移后校验行数一致再退役旧库。
2. **建库内账号**：在 db-mp 建 `feed_curator` 库 + 专用账号
   `feed_curator@'%'`（仅授权该库）。
3. **容器内连接**：`DATABASE_URL=mysql+pymysql://feed_curator:***@db-mp:3306/feed_curator`
   （服务名 `db-mp`，非 localhost/IP）。
4. **改写源 base_url**：feed-curator 容器连 we-mp-rss / RSSHub 仍走宿主机，
   用 `host.docker.internal`：
   - 微信源：环境变量 `WEWE_BASE_URL=http://host.docker.internal:9001` 统一覆盖
     （wewe_client 已支持），免逐条改 DB。
   - RSSHub 源：批量更新 DB 中 `localhost:9002` → `host.docker.internal:9002`。
5. **退役 Homebrew MySQL**：校验迁移无误后 `brew services stop mysql`。

### 运维基线（容器化随附）

- `restart: unless-stopped`：宿主机重启后自动拉起，告别手动 nohup。
- `healthcheck`：探 `/health`，容器异常自动重启；`depends_on` db-mp 健康后再起。
- `.dockerignore`：排除 `.venv`、`.git`、`data/*.db` 等，镜像更小。
- `.env` 注入：敏感配置（DEEPSEEK_API_KEY、DB 密码）经 env_file 注入，不进镜像。
- 镜像基于 `python:3.14-slim` + uv，多阶段构建。

## 风险 / 回滚

- **迁移期数据**：导出—导入期间若仍在抓取，可能丢极少量增量。建议迁移时先停
  feed-curator 抓取（或挑空闲时段），导入后比对行数。
- **回滚**：保留 Homebrew MySQL 数据不立即删除，仅 `stop`。若容器方案有问题，
  改回 `.env` 指向本机 MySQL 并 `brew services start mysql` 即可恢复。
- db-mp 成为唯一数据库，重要性上升。其数据卷 `db_mp_data` 应纳入定期备份。


