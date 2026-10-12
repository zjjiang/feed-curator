#!/bin/bash
# feed-curator 原生运行入口:加载 .env 后前台启动。
# 手动运行:bash scripts/run_server.sh
# launchd(com.feed-curator)以前台进程托管本脚本,KeepAlive 崩溃自动拉起。
cd "$(dirname "$0")/.." || exit 1
set -a
source .env
set +a
exec /opt/homebrew/bin/uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 9003
