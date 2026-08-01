#!/bin/bash
# Start Harbor Job Dashboard server
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOBS_DIR="${HARBOR_JOBS_DIR:-${JOBS_DIR:-$SCRIPT_DIR/../artifacts/jobs}}"
PORT="${PORT:-8092}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
# Loopback by default (the server has no auth). Set HOST=0.0.0.0 to expose it,
# but only on a trusted network or behind an authenticating reverse proxy.
HOST="${HOST:-127.0.0.1}"

cd "$SCRIPT_DIR"

echo "Starting Harbor Job Dashboard..."
echo "  Jobs dir: $JOBS_DIR"
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  Open: http://localhost:$PORT"

exec python3 server.py \
  --jobs-dir "$JOBS_DIR" \
  --host "$HOST" \
  --port "$PORT" \
  --log-level "$LOG_LEVEL"
