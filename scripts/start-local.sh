#!/usr/bin/env bash
# 本机启动 bot。默认走 WebSocket 出站网关，不必开 ngrok。
# 电脑关机或跑 stop-local.sh 即停止。需要 Webhook 时再 START_NGROK=1。
# 使用「python -m uvicorn」，避免项目搬家后 .venv/bin/uvicorn 的 shebang 仍指向旧路径。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NGROK="${NGROK:-ngrok}"
PID_DIR="$ROOT/data"
UV_PID="$PID_DIR/uvicorn.pid"
NG_PID="$PID_DIR/ngrok.pid"
PY="$ROOT/.venv/bin/python"
mkdir -p "$PID_DIR"

# .env 只给当前用户读，避免其它账号扫到密钥。
if [[ -f "$ROOT/.env" ]]; then
  chmod 600 "$ROOT/.env"
fi

if [[ ! -x "$PY" ]]; then
  echo "缺少 .venv，先在项目目录执行: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if ! "$PY" -c "import uvicorn" 2>/dev/null; then
  echo "缺少 uvicorn，请执行: .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi

if [[ -f "$UV_PID" ]] && kill -0 "$(cat "$UV_PID")" 2>/dev/null; then
  echo "uvicorn 已在运行 pid=$(cat "$UV_PID")"
else
  # pid 文件失效时仍可能有旧 uvicorn 占着 8080，先清掉再启动。
  if command -v lsof >/dev/null; then
    for pid in $(lsof -nP -iTCP:8080 -sTCP:LISTEN -t 2>/dev/null); do
      cmd=$(ps -p "$pid" -o args= 2>/dev/null || true)
      if [[ "$cmd" == *"app.main:app"* ]]; then
        kill "$pid" 2>/dev/null || true
        echo "已结束占用 8080 的旧 uvicorn pid=$pid"
        sleep 0.4
      fi
    done
  fi
  cd "$ROOT"
  : >"$PID_DIR/uvicorn.log"
  nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8080 \
    --no-access-log >>"$PID_DIR/uvicorn.log" 2>&1 &
  echo $! >"$UV_PID"
  echo "uvicorn 已启动 pid=$(cat "$UV_PID")"
fi

if [[ "${START_NGROK:-}" == "1" ]]; then
  if [[ ! -x "$NGROK" ]]; then
    echo "找不到 ngrok: $NGROK"
    exit 1
  fi
  if [[ -f "$NG_PID" ]] && kill -0 "$(cat "$NG_PID")" 2>/dev/null; then
    echo "ngrok 已在运行 pid=$(cat "$NG_PID")"
  else
    nohup "$NGROK" http 127.0.0.1:8080 --inspect=false --log=stdout \
      >"$PID_DIR/ngrok.log" 2>&1 &
    echo $! >"$NG_PID"
    echo "ngrok 已启动 pid=$(cat "$NG_PID")"
  fi
fi

ready=0
for _ in $(seq 1 40); do
  if curl -sf http://127.0.0.1:8080/health >/dev/null; then
    ready=1
    break
  fi
  if [[ -f "$UV_PID" ]] && ! kill -0 "$(cat "$UV_PID")" 2>/dev/null; then
    echo "uvicorn 启动后立即退出。最近日志："
    tail -n 40 "$PID_DIR/uvicorn.log" 2>/dev/null || true
    rm -f "$UV_PID"
    exit 1
  fi
  sleep 0.25
done

if [[ "$ready" -ne 1 ]]; then
  echo "健康检查超时（http://127.0.0.1:8080/health）。最近日志："
  tail -n 40 "$PID_DIR/uvicorn.log" 2>/dev/null || true
  exit 1
fi

echo "本机: http://127.0.0.1:8080/health"
echo "接入方式请保持 WebSocket。本进程在跑时后台应变为在线。"

if [[ "${START_NGROK:-}" == "1" ]]; then
  URL=""
  for port in 4040 4041 4042; do
    URL="$(curl -sf "http://127.0.0.1:${port}/api/tunnels" 2>/dev/null \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(next((t['public_url'] for t in d.get('tunnels',[]) if t.get('public_url','').startswith('https://')), ''))" 2>/dev/null || true)"
    if [[ -n "$URL" ]]; then
      break
    fi
    sleep 0.3
  done
  if [[ -n "$URL" ]]; then
    echo "开放平台回调: ${URL}/qq/webhook"
    echo "每次重新开隧道，这个地址可能变，需要再填一次。"
  else
    echo "还没读到 ngrok 公网地址，稍后再看 $PID_DIR/ngrok.log"
  fi
fi
