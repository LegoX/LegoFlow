#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Trainer block layout: training runs land in artifacts/model/<run>/ and console
# logs in artifacts/logs/ (see ../scripts/train.sh).
SAVE_DIR="${SAVE_DIR:-$SCRIPT_DIR/../artifacts/model}"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/../artifacts/logs}"
PORT="${PORT:-8091}"
TUNNEL="${TUNNEL:-false}"

echo "=== LegoFlow-Trainer Training Dashboard ==="
echo "  Save dir: $SAVE_DIR"
echo "  Log dir:  $LOG_DIR"
echo "  Port:     $PORT"
echo "  Tunnel:   $TUNNEL"

# Build frontend if dist/ is missing
if [ ! -f "$SCRIPT_DIR/dist/index.html" ]; then
  echo "[build] Building frontend..."
  (cd "$SCRIPT_DIR" && npm install && npm run build)
fi

# Start the Python API + static server
echo "[server] Starting on :$PORT ..."
python3 "$SCRIPT_DIR/server.py" \
  --port "$PORT" \
  --save-dir "$SAVE_DIR" \
  --log-dir "$LOG_DIR" \
  --static-dir "$SCRIPT_DIR/dist" \
  ${WANDB_ENTITY:+--wandb-entity "$WANDB_ENTITY"} \
  ${WANDB_PROJECT:+--wandb-project "$WANDB_PROJECT"} &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null; exit" INT TERM EXIT

sleep 1
if ! kill -0 $SERVER_PID 2>/dev/null; then
  echo "[error] Server failed to start"
  exit 1
fi
echo "[server] Running (PID $SERVER_PID)"

# Optional Cloudflare quick tunnel for public access (requires cloudflared)
if [ "$TUNNEL" = "true" ]; then
  CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-$(command -v cloudflared 2>/dev/null || true)}"
  if [ -z "$CLOUDFLARED_BIN" ] && [ -x /public/storage/yuxin/cloudflared/bin/cloudflared ]; then
    CLOUDFLARED_BIN=/public/storage/yuxin/cloudflared/bin/cloudflared
  fi
  if [ -z "$CLOUDFLARED_BIN" ]; then
    echo "[tunnel] cloudflared not found; set CLOUDFLARED_BIN or install it. Skipping tunnel." >&2
  else
    TUNNEL_LOG="${TUNNEL_LOG:-/tmp/dashboard-tunnel-$PORT.log}"
    echo "[tunnel] Opening Cloudflare quick tunnel to localhost:$PORT (via $CLOUDFLARED_BIN)"
    echo "[tunnel] cloudflared output → $TUNNEL_LOG"
    "$CLOUDFLARED_BIN" tunnel --url "http://127.0.0.1:$PORT" > "$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!
    trap "kill $SERVER_PID $TUNNEL_PID 2>/dev/null; exit" INT TERM EXIT
    # Wait up to 30s for the public URL to be printed, then surface it clearly.
    TUNNEL_URL=""
    for _ in $(seq 1 30); do
      TUNNEL_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
      [ -n "$TUNNEL_URL" ] && break
      kill -0 $TUNNEL_PID 2>/dev/null || break
      sleep 1
    done
    if [ -n "$TUNNEL_URL" ]; then
      echo ""
      echo "  ╭──────────────────────────────────────────────────────────────╮"
      printf "  │  Public URL: %-47s │\n" "$TUNNEL_URL"
      echo "  ╰──────────────────────────────────────────────────────────────╯"
      echo ""
    else
      echo "[tunnel] URL not detected within 30s — see $TUNNEL_LOG" >&2
    fi
  fi
fi

echo ""
echo "Dashboard ready at http://127.0.0.1:$PORT"
echo "Press Ctrl+C to stop."
wait
