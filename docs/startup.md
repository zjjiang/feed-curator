# 启动速查(整套服务)

一页速查:整套怎么起、怎么停、怎么看状态、起不来怎么查。
架构与原理见 [`deployment.md`](deployment.md)。

## TL;DR

全部是 Docker 容器,带自启策略,**开机后通常自动全部拉起,无需手动操作**。
确认一下就行:

```bash
docker ps --format '{{.Names}}\t{{.Status}}'
# 期望看到 4 个:feed-curator(healthy)、we-mp-rss、rsshub、db-mp
curl -s http://localhost:9003/health    # {"status":"ok"}
```

## 服务与端口

| 服务 | 端口 | 自启 | 起法 |
|------|------|------|------|
| feed-curator | 9003 | unless-stopped | compose(本仓库) |
| we-mp-rss | 9001 | unless-stopped | compose(we-mp-rss 仓库) |
| rsshub | 9002 | always | 独立容器 |
| db-mp (MySQL) | 3306 | always | 独立容器(被前两者共用) |

依赖方向:feed-curator → db-mp + we-mp-rss(:9001) + rsshub(:9002);we-mp-rss → db-mp。
**db-mp 是底座,要最先在跑。**

## 手动启动(若某个没起来)

按依赖顺序。db-mp / rsshub 是独立容器,`docker start` 即可;两个 app 用 compose。

```bash
# 1) 底座:db-mp、rsshub(独立容器,自启;若停了)
docker start db-mp rsshub

# 2) feed-curator
cd ~/Projects/feed-curator
docker compose up -d

# 3) we-mp-rss(微信采集)
cd ~/Projects/we-mp-rss
docker compose -f docker-compose.curator.yml up -d
```

## 日常操作

```bash
# 看日志
cd ~/Projects/feed-curator && docker compose logs -f
cd ~/Projects/we-mp-rss   && docker compose -f docker-compose.curator.yml logs -f

# 重启 / 停止
docker compose restart                                   # feed-curator
docker compose -f docker-compose.curator.yml restart     # we-mp-rss(在其目录)

# 改了 feed-curator 代码后重建
cd ~/Projects/feed-curator && docker compose up -d --build
```

## 验证健康

```bash
curl -s http://localhost:9003/health                       # feed-curator → {"status":"ok"}
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9001/   # we-mp-rss → 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9002/   # rsshub → 200
docker exec db-mp mysqladmin -uroot -p<rootpass> ping       # db-mp → mysqld is alive
```

## 起不来?排障清单

**feed-curator unhealthy / 起不来**
- 看日志:`docker compose logs --tail=50`
- 多半是连不上 db-mp。确认 db-mp 在跑且在 feed-net 上:
  `docker network inspect feed-net --format '{{range .Containers}}{{.Name}} {{end}}'`
  应含 `db-mp feed-curator`。缺了就 `docker network connect feed-net db-mp`。

**we-mp-rss 起不来 / 9001 不通**
- 看日志:`docker compose -f docker-compose.curator.yml logs --tail=50`
- 端口被占?确认没有遗留的本机进程抢 9001:
  `lsof -nP -iTCP:9001 -sTCP:LISTEN`(应只有 docker)。
- 镜像没了(换机后)?见 deployment.md「国内网络下的镜像获取」第 2 节,
  用南大代理拉 `ghcr.io/rachelos/we-mp-rss` 再 retag。

**端口冲突 / 连错库**
- 3306 应只有 db-mp 监听。若本机 Homebrew MySQL/redis 又被启动会冲突:
  `brew services list | grep -E 'mysql|redis'`,应为 `none`(已退役)。

**feed-net 不存在(换机/重置后)**
```bash
docker network create feed-net
docker network connect feed-net db-mp
# 两个 app compose 都声明 feed-net 为 external,会自动接入
```

## 数据 / 登录态(不会随容器丢)

- 文章数据:db-mp 的数据卷,容器删了也在。
- we-mp-rss 微信登录态:`~/Projects/we-mp-rss/data`(`.secret_key`、`wx.lic`),
  挂载进容器,重建容器**不用重新扫码**。
