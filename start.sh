#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== feed-curator (本机开发模式) ==="
echo "提示: 生产部署请用 Docker — docker compose up -d --build (见 docs/deployment.md)"
echo ""

# 从 .env 读取敏感配置(DATABASE_URL / DEEPSEEK_API_KEY)。.env 不提交。
# 注意: .env 里的 DATABASE_URL 指向容器网络的 db-mp,本机直跑连不上。
# 本机开发请在下面用 127.0.0.1 覆盖,或临时改 .env。
if [ -f .env ]; then
    set -a; . ./.env; set +a
    echo "[ok] 已加载 .env"
else
    echo "[warn] 缺少 .env(参考 .env.example 创建);无 DATABASE_URL 时将回退 SQLite"
fi

# 本机直跑时,DATABASE_URL 若指向 db-mp(容器名)需改回 127.0.0.1。
case "$DATABASE_URL" in
    *@db-mp:*)
        echo "[warn] DATABASE_URL 指向容器名 db-mp,本机直跑无法解析。"
        echo "       请改用 docker compose,或临时 export 指向 127.0.0.1 的 DATABASE_URL。"
        ;;
esac

# 确保 we-mp-rss 在跑（微信源依赖它）
if curl -s -o /dev/null -w "%{http_code}" http://localhost:9001/ 2>/dev/null | grep -q "200"; then
    echo "[ok] we-mp-rss 已在 :9001 运行"
else
    echo "[warn] we-mp-rss 未运行，微信源将不可用"
fi

echo "启动于 :9003"
exec uv run --no-sync uvicorn app.main:app --port 9003 --host 0.0.0.0
