#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== feed-curator ==="
echo "启动中..."

# 确保 we-mp-rss 在跑（微信源依赖它）
if curl -s -o /dev/null -w "%{http_code}" http://localhost:9001/ 2>/dev/null | grep -q "200"; then
    echo "[ok] we-mp-rss 已在 :9001 运行"
else
    echo "[warn] we-mp-rss 未运行，微信源将不可用"
fi

# 启动 feed-curator
exec uv run uvicorn app.main:app --port 8002 --host 0.0.0.0
