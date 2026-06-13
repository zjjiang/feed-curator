# feed-curator

AI 驱动的个人内容聚合平台。多源采集 + AI 评分筛选 + Web 界面。

## 架构

```
we-mp-rss (:9001)  ──►  feed-curator (:8002)  ◄──  RSSHub (:1200)
   微信采集                 采集 + AI + Web            RSS 桥接
```

## 部署（Mac Mini）

### 前置条件

- macOS + Python 3.14+
- uv：`curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker：`brew install --cask docker`

### 1. 拉代码 + 装依赖

```bash
cd ~/Projects
git clone git@github.com:zjjiang/feed-curator.git
cd feed-curator
uv sync
```

### 2. 启动 RSSHub（虎嗅等源的桥接）

```bash
docker run -d \
  --name rsshub \
  --restart always \
  -p 1200:1200 \
  -e CACHE_TYPE=memory \
  -e CACHE_EXPIRE=600 \
  diygod/rsshub:latest
```

### 3. 启动 we-mp-rss（微信源，可选）

确保 we-mp-rss 在 `:9001` 运行。没有则跳过，其他源不受影响。

### 4. 启动 feed-curator

```bash
# 不带 AI 评分
uv run uvicorn app.main:app --port 8002 --host 0.0.0.0

# 带 AI 评分
DEEPSEEK_API_KEY="sk-你的key" uv run uvicorn app.main:app --port 8002 --host 0.0.0.0

# 后台运行
nohup env DEEPSEEK_API_KEY="sk-xxx" uv run uvicorn app.main:app --port 8002 --host 0.0.0.0 > /tmp/feed-curator.log 2>&1 &
```

### 5. 验证

```bash
curl http://localhost:8002/health
# {"status":"ok"}

open http://localhost:8002/
```

### 6. 添加订阅源

```bash
# 36氪
curl -X POST http://localhost:8002/api/sources \
  -H 'Content-Type: application/json' \
  -d '{"type":"rss","name":"36氪","config":{"feed_url":"https://36kr.com/feed"},"fetch_interval_min":30}'

# 虎嗅（经 RSSHub）
curl -X POST http://localhost:8002/api/sources \
  -H 'Content-Type: application/json' \
  -d '{"type":"rss","name":"虎嗅","config":{"feed_url":"http://localhost:1200/huxiu/article"},"fetch_interval_min":30}'

# 钛媒体
curl -X POST http://localhost:8002/api/sources \
  -H 'Content-Type: application/json' \
  -d '{"type":"rss","name":"钛媒体","config":{"feed_url":"https://www.tmtpost.com/rss.xml"},"fetch_interval_min":30}'

# 微信公众号（需 we-mp-rss）
curl -X POST http://localhost:8002/api/sources \
  -H 'Content-Type: application/json' \
  -d '{"type":"wechat","name":"公众号名","config":{"mp_id":"MP_WXS_xxx","wewe_base_url":"http://localhost:9001"},"fetch_interval_min":30}'

# arXiv
curl -X POST http://localhost:8002/api/sources \
  -H 'Content-Type: application/json' \
  -d '{"type":"arxiv","name":"arXiv cs.AI","config":{"category":"cs.AI","max_results":20},"fetch_interval_min":360}'
```

或直接在 http://localhost:8002/sources 页面操作。

### 7. 开机自启（可选）

```bash
cat > ~/Library/LaunchAgents/com.feed-curator.plist << 'LAUNCHEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.feed-curator</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/jiangzhijian/.local/bin/uv</string>
        <string>run</string>
        <string>uvicorn</string>
        <string>app.main:app</string>
        <string>--port</string>
        <string>8002</string>
        <string>--host</string>
        <string>0.0.0.0</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/jiangzhijian/Projects/feed-curator</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DEEPSEEK_API_KEY</key>
        <string>sk-你的key</string>
    </dict>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/feed-curator.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/feed-curator.log</string>
</dict>
</plist>
LAUNCHEOF

launchctl load ~/Library/LaunchAgents/com.feed-curator.plist
```

## 端口汇总

| 服务 | 端口 | 用途 |
|------|------|------|
| feed-curator | 8002 | 主服务（Web UI + API） |
| we-mp-rss | 9001 | 微信采集（可选） |
| RSSHub | 1200 | RSS 桥接（虎嗅等） |

## 数据

| 文件 | 说明 |
|------|------|
| `data/feed-curator.db` | SQLite 数据库（所有文章） |
| `/tmp/feed-curator.log` | 运行日志 |

## 常用命令

```bash
# 手动触发 AI 评分
curl -X POST http://localhost:8002/api/score

# 查看文章总数
curl -s 'http://localhost:8002/api/items?limit=1' | python3 -c "import json,sys; print(json.load(sys.stdin)['total'])"

# 停止服务
pkill -f "uvicorn app.main:app --port 8002"

# 查看日志
tail -f /tmp/feed-curator.log
```

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | 否 | DeepSeek API key，不配则 AI 评分不启用 |
