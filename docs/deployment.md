# feed-curator 部署架构

本文档描述 feed-curator 的部署架构与运维。**状态:容器化已落地**
(2026-06,feed-curator 已容器化,数据库已合并进 db-mp,本机 Homebrew MySQL 已退役)。

> 适用范围:本仓库(feed-curator)。we-mp-rss、RSSHub 为独立项目,本文仅描述与它们的集成关系。

## 服务全景(当前)

| 服务 | 端口 | 角色 | 运行方式 |
|------|------|------|---------|
| feed-curator | 9003 | 采集 + AI 评分 + Web + MCP | **Docker (restart=unless-stopped)** |
| we-mp-rss | 9001 | 微信公众号采集 | **Docker (restart=unless-stopped)** |
| RSSHub | 9002 | RSS 桥接(虎嗅等) | Docker (restart=always) |
| db-mp (Docker MySQL) | 3306 | **`feed_curator` + `we_mp_rss` 两库** | Docker (restart=always) |
| ~~Homebrew MySQL~~ | ~~3306~~ | ~~已退役(stop,数据保留供回滚)~~ | ~~brew services~~ |
| ~~Homebrew redis~~ | ~~6379~~ | ~~已退役;we-mp-rss 改用镜像内置 redis~~ | ~~brew services~~ |
| ~~singbox~~ | - | ~~未启用(PROXY_ENABLED=False,宿主另有 Clash)~~ | - |

> we-mp-rss 容器化细节见文末「we-mp-rss 容器化」一节。它是上游仓库
> (github.com/rachelos/we-mp-rss),定制 compose 未提交上游,留在本机
> `~/Projects/we-mp-rss/docker-compose.curator.yml`。

## 演进前架构(历史,迁移前)

> 保留作为背景。下面 4 个痛点正是本次容器化要解决的;迁移后均已消除。

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

### 演进前痛点(均已解决)

1. **3306 端口冲突**:Homebrew MySQL(IPv4 `127.0.0.1:3306`)与 db-mp(IPv6 `*:3306`)
   抢同一端口,连谁取决于 IPv4/IPv6 解析。→ 合并到 db-mp 后,3306 仅一个监听。
2. **重启全靠手动**:feed-curator、we-mp-rss 都是 `nohup`,机器重启后服务全部丢失。
   → feed-curator 容器 `restart=unless-stopped` 自动拉起。
3. **启动姿势是隐性知识**:we-mp-rss 必须 `PORT=9001` + `DB=...@[::1]:3306/...` 才能起。
   → feed-curator 启动收敛到 `docker compose up`,本文档记录全流程。
4. **配置散落**:DB 里微信源 / RSS 源写死了 `localhost:9001/9002`。
   → 已批量改写为 `host.docker.internal`。

## 当前架构(已落地)

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
                          │         ▲ host.docker.internal              │
                          │         │                                  │
                          │   we-mp-rss(:9001)   RSSHub(:9002)          │
                          │   (本机/容器,经 host.docker.internal 访问)  │
                          │                                            │
                          │   ✗ Homebrew MySQL 已退役(stop)             │
                          └──────────────────────────────────────────┘
```

容器连接关系:
- **数据库**:feed-curator 与 db-mp 同在 `feed-net` 网络,用服务名 `db-mp:3306`
  直连,不经宿主机端口,无 IPv4/IPv6 之分 —— 3306 冲突从根上消失。
- **we-mp-rss / RSSHub**:仍在宿主机,容器经 `host.docker.internal` 访问。

## 日常运维

```bash
cd ~/Projects/feed-curator
docker compose up -d --build      # 起/更新容器(改了代码后重建)
docker compose logs -f            # 看日志
docker compose restart            # 重启
docker compose down               # 停并删容器(数据在 db-mp,不受影响)
curl http://localhost:9003/health # {"status":"ok"}
```

容器带 `restart: unless-stopped`,宿主机重启后自动拉起。

## 从零重建(灾备 / 换机)

> 关键前提:本网络下境外 registry 不可达,**必须先配国内加速**,否则镜像基础层拉不下来。

1. **配 Docker registry 加速器**(系统级,一次性)。`~/.docker/daemon.json` 加:
   ```json
   "registry-mirrors": [
     "https://docker.1ms.run",
     "https://docker.m.daocloud.io",
     "https://dockerproxy.com",
     "https://docker.nju.edu.cn"
   ]
   ```
   改完重启 Docker Desktop,`docker info | grep -A4 "Registry Mirrors"` 确认生效。

2. **建网络并接入 db-mp**:
   ```bash
   docker network create feed-net
   docker network connect feed-net db-mp
   ```

3. **在 db-mp 建库 + 账号**(若是全新 db-mp):
   ```sql
   CREATE DATABASE feed_curator CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'feed_curator'@'%' IDENTIFIED BY '<密码>';
   GRANT ALL PRIVILEGES ON feed_curator.* TO 'feed_curator'@'%';
   ```

4. **配 `.env`**(从 `.env.example` 复制):
   ```
   DATABASE_URL=mysql+pymysql://feed_curator:<密码>@db-mp:3306/feed_curator?charset=utf8mb4
   WEWE_BASE_URL=http://host.docker.internal:9001
   DEEPSEEK_API_KEY=sk-...
   ```

5. **构建并启动**:`docker compose up -d --build`

6. **(可选)起 we-mp-rss 容器**(微信采集):
   ```bash
   cd ~/Projects/we-mp-rss
   # 镜像经南大代理拉取后 retag(见下「国内网络下的镜像获取」第 2 节)
   docker compose -f docker-compose.curator.yml up -d
   ```
   依赖:db-mp 里有 `we_mp_rss` 库 + `rss_user@'%'`;`./data` 目录(登录态)。

### 国内网络下的镜像获取(踩过的坑,通用参考)

本机网络到境外 registry 几乎不可用,下面是这套环境里验证过的可靠姿势。

**1. Docker Hub(docker.io)镜像 —— 配 daemon registry-mirrors**

`~/.docker/daemon.json` 加 `registry-mirrors`(见上「从零重建」第 1 步),改完重启
Docker Desktop。之后用**裸镜像名**(`FROM python:3.14-slim-bookworm`、
`docker pull redis:7-alpine`)即可,daemon 自动路由到加速器。
- 大层(几十 MB)**慢但能完成**;多个并发拉取会抢带宽反而更慢 —— 让单个构建/拉取独占。
- 不配 mirror 直接 `docker pull docker.io/...`,大层会零字节卡死。

**2. ghcr.io(GitHub Container Registry)镜像 —— registry-mirrors 不管用**

⚠️ daemon 的 registry-mirrors **只代理 docker.io,不代理 ghcr.io**。ghcr 镜像
(如 `ghcr.io/rachelos/we-mp-rss`)直连会一直 `Retrying`。改走**南大 ghcr 代理**,
拉完 retag 回原名供 compose/Dockerfile 引用:
```bash
docker pull ghcr.nju.edu.cn/<owner>/<image>:<tag>
docker tag  ghcr.nju.edu.cn/<owner>/<image>:<tag>  ghcr.io/<owner>/<image>:<tag>
```
- daocloud 的 ghcr 代理(`ghcr.m.daocloud.io`)有白名单,多数镜像 `not in allowlist`,不可靠。
- 测代理是否可用:`docker pull <代理前缀>/... ` 看有没有 `Download complete`(manifest
  可达不代表层能下,要看层)。

**3. 镜像内的二级依赖 —— 在 Dockerfile 里换国内源**

镜像构建时 apt / pip 仍会访问境外,需在 Dockerfile 内显式换源:
- **apt**:`sed` 把 `deb.debian.org` / `security.debian.org` 换成
  `mirrors.tuna.tsinghua.edu.cn`(否则拉 main Packages 索引会卡死)。
- **pip / uv**:`PIP_INDEX_URL` / `UV_INDEX_URL` = `https://pypi.tuna.tsinghua.edu.cn/simple`。
- **playwright** 内核:`npmmirror.com/mirrors/playwright`。

**4. 其他**
- GitHub 仓库:clone 走 **SSH**(HTTPS 常超时);we-mp-rss 仓库的 origin 用了
  `gh-proxy.com` 前缀代理。
- 镜像源清单(本环境验证过):docker.io → `docker.1ms.run` / `docker.m.daocloud.io`;
  ghcr → `ghcr.nju.edu.cn`;PyPI/apt → 清华 tuna。

## 数据备份

db-mp 现在是唯一数据库,重要性上升。备份其数据卷 / 逻辑导出:

```bash
# 逻辑备份 feed_curator 库
docker exec db-mp mysqldump -uroot -p<rootpass> --single-transaction \
  --default-character-set=utf8mb4 --no-tablespaces feed_curator > feed_curator_$(date +%F).sql
```

## 风险 / 回滚

- **回滚到本机 MySQL**:Homebrew MySQL 数据未删,仅 `stop`。如需回退:
  `brew services start mysql`,把 `.env` 的 `DATABASE_URL` 改回
  `mysql+pymysql://root:***@127.0.0.1:3306/feed_curator?charset=utf8mb4`,本机直跑
  (`./start.sh`)或在 compose 里用 `host.docker.internal:3306`。
- db-mp 成为单点,数据卷 `db_mp_data` 应纳入定期备份(见上)。
- 老数据保留期:确认容器稳定运行一段时间后,可手动清理 Homebrew MySQL 的旧
  `feed_curator` 库(`brew services start mysql` → `DROP DATABASE` → 再 stop)。

## 迁移记录(2026-06)

- 从本机 Homebrew MySQL 9.3.0 迁出 `feed_curator` 库(约 3968 篇,26MB dump),
  导入 db-mp;行数校验一致(items 3968 / sources 30 / jobs 346 / settings 1)。
- 在 db-mp 建专用账号 `feed_curator@'%'`(仅授权该库,不放开 root)。
- 改写 DB 中 17 个源的 base_url:微信源 9 个 `localhost:9001` + RSS 源 8 个
  `localhost:9002` → `host.docker.internal`(因 wechat 适配器以源 config 里的
  `wewe_base_url` 优先,环境变量覆盖不到抓取路径,故必须改 DB)。
- 退役 Homebrew MySQL(`brew services stop mysql`),3306 仅剩 db-mp 监听。

## we-mp-rss 容器化(2026-06)

we-mp-rss 同样容器化了,接入同一套 db-mp / feed-net,与 feed-curator 一致。

**为何不用官方 compose**:官方 `compose/docker-compose.yaml` 会自建 mysql/redis/
singbox 四件套,与现有手动管理的 db-mp 冲突、端口也按 8001。故另写一份定制 compose
`~/Projects/we-mp-rss/docker-compose.curator.yml`(we-mp-rss 是上游仓库,此文件不
提交上游,仅本机保留)。

**镜像来源**:`ghcr.io/rachelos/we-mp-rss:latest`,经南大 ghcr 代理拉取(3.4GB)
再 retag —— 见「国内网络下的镜像获取」第 2 节。

**实际依赖(摸排后精简)**:
- **DB**:经 feed-net 用服务名连 `db-mp:3306`,账号 `rss_user`,库 `we_mp_rss`。
  容器源 IP 命中 `rss_user@'%'` 授权 —— 绕开了本机进程那个「必须用 localhost、
  127.0.0.1 反向 DNS 会 Access denied」的坑。
- **redis**:镜像**自带内置 redis**,数据持久化在挂载的 `./data/redis/dump.rdb`。
  故**不需要**外挂 redis 容器,本机 Homebrew redis 已退役(`brew services stop redis`)。
- **代理 singbox**:`.env` 里 `PROXY_ENABLED=False`(宿主另有 Clash Verge),不纳入。
- **Xvfb/显示**:`HEADLESS=true`,运行时无需虚拟显示。
- **登录态**:存于 `./data`(`.secret_key`、`wx.lic`、`cache`),挂载进容器即复用,
  **无需重新扫码**。

**compose 要点**(`docker-compose.curator.yml`):`PORT=9001`、`HEADLESS=true`、
`AUTO_RELOAD=False`(关热重载,单进程,消除内置 redis 端口重复占用 warn)、
`TZ=Asia/Shanghai`、挂 `./data`、接 `feed-net`(external)。

**启停**:
```bash
cd ~/Projects/we-mp-rss
docker compose -f docker-compose.curator.yml up -d
docker compose -f docker-compose.curator.yml logs -f
```

**验证结果**:容器 :9001 API 200;经 feed-curator 触发微信源抓取端到端打通
(`GET /feed/MP_WXS_*.json 200`),登录态有效。本机 nohup 进程已停,无 launchd
自启,重启不会与容器抢端口。


