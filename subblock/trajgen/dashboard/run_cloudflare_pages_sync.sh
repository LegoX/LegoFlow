#!/usr/bin/env bash
# Loop-generate the trajgen dashboard and publish dashboard/site/ to Cloudflare
# Pages via wrangler, so progress is viewable remotely. Mirrors the swegen
# branch reference (subblock/swegen/dashboard/run_cloudflare_pages_sync.sh), adapted to
# trajgen's progress_monitor.py (no --state-file).
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${TRAJGEN_HOME:-$HOME}"
DASHBOARD_SCRIPT="$SCRIPT_DIR/progress_monitor.py"
RUN_DIR="$SCRIPT_DIR"
BLOCK_DIR="$(cd "$RUN_DIR/.." && pwd)"
PUBLIC_DIR="${PUBLIC_DIR:-$RUN_DIR/site}"
CACHE_FILE="${CACHE_FILE:-$RUN_DIR/.cache/.progress_monitor_cache.json}"
ENV_FILE="${ENV_FILE:-$HOME_DIR/.config/trajgen_progress_cloudflare.env}"

PROJECT_NAME="${PROJECT_NAME:-swe-trajgen-databoard}"
BRANCH_NAME="${BRANCH_NAME:-trajgen}"
LOOP_SECONDS="${LOOP_SECONDS:-3600}"
PORT="${PORT:-8770}"
# wrangler v4+ requires Node.js >= 22; pin to v3 so it also runs on Node 18.
# Override (e.g. WRANGLER_PKG=wrangler) if a newer Node.js is available.
WRANGLER_PKG="${WRANGLER_PKG:-wrangler@3}"

# SFT conversion in the loop: re-run convert_trajectories.sh (skip when
# unchanged) at most every CONVERT_EVERY_SECONDS, so the dashboard's SFT stats
# stay fresh online without coupling the heavy conversion to the deploy cadence.
CONVERT_SCRIPT="$BLOCK_DIR/scripts/convert_trajectories.sh"
CONVERT_ENABLED="${CONVERT_ENABLED:-1}"
CONVERT_JOB="${CONVERT_JOB:-latest}"
CONVERT_EVERY_SECONDS="${CONVERT_EVERY_SECONDS:-7200}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

# Map a Harbor job name to a swe_data_process scaffold key (same heuristic the
# dashboard uses). config.yaml's agent.name may not match the live job, so we
# derive from the job dir name and pass --scaffold explicitly.
detect_scaffold() {
  case "$1" in
    *openhands[-_]sdk*) echo "openhands_sdk" ;;
    *claude[-_]code*)   echo "claude_code" ;;
    *open[-_]code*|*opencode*) echo "open_code" ;;
    *terminus*2*)       echo "terminus2" ;;
    *)                  echo "" ;;
  esac
}

# Resolve which job conversion targets (for scaffold detection): the configured
# job name, or the most recently modified dir under artifacts/jobs for "latest".
resolve_convert_job() {
  if [[ "$CONVERT_JOB" == "latest" ]]; then
    ls -1t "$BLOCK_DIR/artifacts/jobs" 2>/dev/null | head -n 1
  else
    echo "$CONVERT_JOB"
  fi
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

LOCK_FILE="${LOCK_FILE:-/tmp/trajgen_dashboard_sync.lock}"
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
LAST_CONVERT=0

while true; do
  now="$(date +%s)"
  if [[ "$CONVERT_ENABLED" == "1" && $((now - LAST_CONVERT)) -ge "$CONVERT_EVERY_SECONDS" ]]; then
    if [[ -f "$CONVERT_SCRIPT" ]]; then
      job_for_detect="$(resolve_convert_job)"
      scaffold_args=()
      sc="$(detect_scaffold "$job_for_detect")"
      [[ -n "$sc" ]] && scaffold_args=(--scaffold "$sc")
      log "converting trajectories (job=$CONVERT_JOB scaffold=${sc:-auto}; skip if unchanged)"
      if bash "$CONVERT_SCRIPT" --job "$CONVERT_JOB" "${scaffold_args[@]}" --skip-unchanged; then
        log "conversion done or skipped"
      else
        log "conversion failed; continuing with existing sft_data"
      fi
    else
      log "convert script not found at $CONVERT_SCRIPT; skipping conversion"
    fi
    LAST_CONVERT="$now"
  fi

  log "generating dashboard HTML"
  if uv run --no-project --script "$DASHBOARD_SCRIPT" \
      --output-html "$PUBLIC_DIR/index.html" \
      --cache-file "$CACHE_FILE" \
      --refresh "$LOOP_SECONDS" \
      --force-full-scan; then
    log "generated $PUBLIC_DIR/index.html"
  else
    log "generation failed; will retry after $LOOP_SECONDS seconds"
    sleep "$LOOP_SECONDS"
    continue
  fi

  if [[ "$PROJECT_READY" -eq 0 ]]; then
    log "ensuring Cloudflare Pages project $PROJECT_NAME exists"
    npx --yes "$WRANGLER_PKG" pages project create "$PROJECT_NAME" \
      --production-branch "$BRANCH_NAME" >/tmp/trajgen_pages_project_create.log 2>&1 || true
    PROJECT_READY=1
  fi

  log "deploying dashboard to Cloudflare Pages project $PROJECT_NAME"
  npx --yes "$WRANGLER_PKG" pages deploy "$PUBLIC_DIR" \
    --project-name "$PROJECT_NAME" \
    --branch "$BRANCH_NAME" \
    --commit-dirty=true \
    --commit-message "Update trajgen progress dashboard $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

  log "sleeping $LOOP_SECONDS seconds before next refresh"
  sleep "$LOOP_SECONDS"
done
