# feed-curator

AI 驱动的个人内容聚合平台。多源采集 + AI 评分筛选 + Web 界面。

## 架构

```
we-mp-rss (:9001)  ──►  feed-curator (:9003)  ◄──  RSSHub (:9002)
   微信采集                 采集 + AI + Web            RSS 桥接
                              │
                              ▼
                       db-mp (MySQL, Docker)
                  feed_curator + we_mp_rss 两个库
```

feed-curator 以 Docker 容器运行,数据库统一在 `db-mp` 容器。完整部署架构、
现状演进与运维基线见 [`docs/deployment.md`](docs/deployment.md)。

## 部署（Docker，推荐）

### 前置条件

- macOS + Docker Desktop（`brew install --cask docker`）
- `db-mp` MySQL 容器在跑（we-mp-rss 项目提供），并已建 `feed_curator` 库
- 国内网络:已配 Docker `registry-mirrors`（见 docs/deployment.md）

### 启动

```bash
cd ~/Projects/feed-curator
cp .env.example .env        # 填入 DATABASE_URL（指向 db-mp）、DEEPSEEK_API_KEY
docker network create feed-net 2>/dev/null; docker network connect feed-net db-mp 2>/dev/null
docker compose up -d --build
curl http://localhost:9003/health      # {"status":"ok"}
open http://localhost:9003/
```

容器带 `restart: unless-stopped` + healthcheck,开机自启,无需 launchd。

### 本机直跑（开发备选）

```bash
uv sync
export DATABASE_URL="mysql+pymysql://USER:PASSWORD@127.0.0.1:3306/feed_curator?charset=utf8mb4"
uv run --no-sync uvicorn app.main:app --port 9003 --host 0.0.0.0
```

## 端口汇总

| 服务 | 端口 | 用途 |
|------|------|------|
| feed-curator | 9003 | 主服务（Web UI + API），Docker 容器 |
| we-mp-rss | 9001 | 微信采集（可选），本机进程 |
| RSSHub | 9002 | RSS 桥接（虎嗅等），Docker 容器 |
| db-mp (MySQL) | 3306 | 数据库,`feed_curator` + `we_mp_rss` 两库 |

## 数据

| 位置 | 说明 |
|------|------|
| `db-mp` 容器 `feed_curator` 库 | 所有文章（MySQL,数据卷 `db_mp_data`） |
| `docker compose logs` | 运行日志 |

## 常用命令

```bash
# 手动触发 AI 评分
curl -X POST http://localhost:9003/api/score

# 查看文章总数
curl -s 'http://localhost:9003/api/items?limit=1' | python3 -c "import json,sys; print(json.load(sys.stdin)['total'])"

# 重启 / 停止容器
docker compose restart
docker compose down

# 查看日志
docker compose logs -f
```

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | 否 | DeepSeek API key，不配则 AI 评分不启用 |
