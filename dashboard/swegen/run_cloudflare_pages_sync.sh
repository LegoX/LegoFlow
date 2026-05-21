#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${SWEGEN_HOME:-/home/ywxzml3j/ywxzml3juser23}"
DASHBOARD_SCRIPT="$SCRIPT_DIR/progress_monitor_all.py"
RUN_DIR="$SCRIPT_DIR"
PUBLIC_DIR="${PUBLIC_DIR:-$RUN_DIR/site}"
STATE_FILE="${STATE_FILE:-$RUN_DIR/.progress_monitor_all_state.jsonl}"
CACHE_FILE="${CACHE_FILE:-$RUN_DIR/.progress_monitor_all_cache.json}"
ENV_FILE="${ENV_FILE:-$HOME_DIR/.config/swegen_progress_cloudflare.env}"

PROJECT_NAME="${PROJECT_NAME:-swe-databoard}"
BRANCH_NAME="${BRANCH_NAME:-swegen}"
LOOP_SECONDS="${LOOP_SECONDS:-3600}"
PORT="${PORT:-8000}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" || -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
  log "missing CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID in $ENV_FILE"
  exit 1
fi
export CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID

mkdir -p "$PUBLIC_DIR"

LOCK_FILE="${LOCK_FILE:-/tmp/swegen_dashboard_sync.lock}"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another dashboard sync is already running; exit"
  exit 1
fi

log "starting local dashboard server at http://127.0.0.1:$PORT/index.html"
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$PUBLIC_DIR" >/dev/null 2>&1 &
SERVER_PID=$!
sleep 1
if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
  log "failed to start local dashboard server on port $PORT"
  exit 1
fi

cleanup() {
  kill "$SERVER_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM

PROJECT_READY=0

while true; do
  log "generating dashboard HTML"
  if python3 "$DASHBOARD_SCRIPT" \
      --output-html "$PUBLIC_DIR/index.html" \
      --state-file "$STATE_FILE" \
      --cache-file "$CACHE_FILE"; then
    log "generated $PUBLIC_DIR/index.html"
  else
    log "generation failed; will retry after $LOOP_SECONDS seconds"
    sleep "$LOOP_SECONDS"
    continue
  fi

  if [[ "$PROJECT_READY" -eq 0 ]]; then
    log "ensuring Cloudflare Pages project $PROJECT_NAME exists"
    npx --yes wrangler pages project create "$PROJECT_NAME" \
      --production-branch "$BRANCH_NAME" >/tmp/swegen_pages_project_create.log 2>&1 || true
    PROJECT_READY=1
  fi

  log "deploying dashboard to Cloudflare Pages project $PROJECT_NAME"
  npx --yes wrangler pages deploy "$PUBLIC_DIR" \
    --project-name "$PROJECT_NAME" \
    --branch "$BRANCH_NAME" \
    --commit-dirty=true \
    --commit-message "Update SWE-gen progress dashboard $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

  log "sleeping $LOOP_SECONDS seconds before next refresh"
  sleep "$LOOP_SECONDS"
done
