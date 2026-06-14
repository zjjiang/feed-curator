#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== feed-curator ==="

# 从 .env 读取敏感配置(DATABASE_URL / DEEPSEEK_API_KEY)。.env 不提交。
if [ -f .env ]; then
    set -a; . ./.env; set +a
    echo "[ok] 已加载 .env"
else
    echo "[warn] 缺少 .env(参考 .env.example 创建);无 DATABASE_URL 时将回退 SQLite"
fi

# 确保本机 MySQL 在跑
if mysql -uroot -pjzj -e "SELECT 1;" >/dev/null 2>&1; then
    echo "[ok] 本机 MySQL 可连"
else
    echo "[warn] 本机 MySQL 连不上，检查: brew services list | grep mysql"
fi

# 确保 we-mp-rss 在跑（微信源依赖它）
if curl -s -o /dev/null -w "%{http_code}" http://localhost:9001/ 2>/dev/null | grep -q "200"; then
    echo "[ok] we-mp-rss 已在 :9001 运行"
else
    echo "[warn] we-mp-rss 未运行，微信源将不可用"
fi

echo "启动于 :9003，数据库 = MySQL/feed_curator"
exec uv run --no-sync uvicorn app.main:app --port 9003 --host 0.0.0.0
