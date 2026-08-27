#!/usr/bin/env bash
# 停止本机 bot 与 ngrok（电脑不用关机也可以先停服务）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_DIR="$ROOT/data"

# 按 pid 文件结束进程；文件缺失或进程已死则跳过。
stop_pidfile() {
  local file="$1"
  local name="$2"
  if [[ ! -f "$file" ]]; then
    echo "$name 未记录 pid"
    return
  fi
  local pid
  pid="$(cat "$file")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    echo "已停止 $name pid=$pid"
  else
    echo "$name 已不在运行"
  fi
  rm -f "$file"
}

stop_pidfile "$PID_DIR/uvicorn.pid" "uvicorn"
stop_pidfile "$PID_DIR/ngrok.pid" "ngrok"
