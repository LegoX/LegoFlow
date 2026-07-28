#!/usr/bin/env bash
# Loop-generate the tracer dashboard and publish dashboard/site/ to Cloudflare
# Pages via wrangler, so progress is viewable remotely. Mirrors the swegen
# branch reference (subblock/curator/dashboard/run_cloudflare_pages_sync.sh), adapted to
# tracer's progress_monitor.py (no --state-file).
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${TRAJGEN_HOME:-$HOME}"
DASHBOARD_SCRIPT="$SCRIPT_DIR/progress_monitor.py"
RUN_DIR="$SCRIPT_DIR"
BLOCK_DIR="$(cd "$RUN_DIR/.." && pwd)"
PUBLIC_DIR="${PUBLIC_DIR:-$RUN_DIR/site}"
CACHE_FILE="${CACHE_FILE:-$RUN_DIR/.cache/.progress_monitor_cache.json}"
ENV_FILE="${ENV_FILE:-$HOME_DIR/.config/trajgen_progress_cloudflare.env}"

PROJECT_NAME="${PROJECT_NAME:-swe-tracer-databoard}"
BRANCH_NAME="${BRANCH_NAME:-tracer}"
LOOP_SECONDS="${LOOP_SECONDS:-3600}"
PORT="${PORT:-8770}"
# wrangler v4+ requires Node.js >= 22; pin to v3 so it also runs on Node 18.
# Override (e.g. WRANGLER_PKG=wrangler) if a newer Node.js is available.
WRANGLER_PKG="${WRANGLER_PKG:-wrangler@3}"
WRANGLER_WORKDIR="${WRANGLER_WORKDIR:-/tmp/trajgen-wrangler-workdir}"
export CI="${CI:-1}"

# Dashboard payload controls. Keep samples enabled by default for local parity,
# but allow public sync deployments to shrink or omit embedded previews.
DASHBOARD_INCLUDE_SAMPLES="${DASHBOARD_INCLUDE_SAMPLES:-1}"
DASHBOARD_SAMPLE_LIMIT="${DASHBOARD_SAMPLE_LIMIT:-200}"
DASHBOARD_SAMPLE_PREVIEW_CHARS="${DASHBOARD_SAMPLE_PREVIEW_CHARS:-1200}"
DASHBOARD_SAMPLE_MESSAGE_LIMIT="${DASHBOARD_SAMPLE_MESSAGE_LIMIT:-12}"
DASHBOARD_LOCAL_MODE="${DASHBOARD_LOCAL_MODE:-public}"
DASHBOARD_HARBOR_JOBS_DIR="${DASHBOARD_HARBOR_JOBS_DIR:-/storage/jierun/code/harbor/jobs}"
DASHBOARD_MAX_TRIALS_PER_JOB="${DASHBOARD_MAX_TRIALS_PER_JOB:-0}"
DASHBOARD_MAX_QUALITY_RECORDS_PER_DATASET="${DASHBOARD_MAX_QUALITY_RECORDS_PER_DATASET:-0}"

# Optional full-trajectory publishing. Static metrics always deploy through
# Pages. Set TRACER_R2_UPLOAD=1 and TRACER_R2_BUCKET=<bucket> to upload local
# Harbor trajectory JSON files referenced by data/traj_cards.jsonl. Bind the
# same bucket to Pages as TRACER_TRAJ_BUCKET for /api/traj.
TRACER_R2_UPLOAD="${TRACER_R2_UPLOAD:-0}"
TRACER_R2_BUCKET="${TRACER_R2_BUCKET:-}"
TRACER_R2_UPLOAD_LIMIT="${TRACER_R2_UPLOAD_LIMIT:-0}"
TRACER_R2_UPLOAD_CURSOR_FILE="${TRACER_R2_UPLOAD_CURSOR_FILE:-$RUN_DIR/.cache/.r2_upload_cursor}"
R2_MANIFEST_SCRIPT="$SCRIPT_DIR/export_r2_manifest.py"

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

wrangler() {
  mkdir -p "$WRANGLER_WORKDIR"
  (cd "$WRANGLER_WORKDIR" && npx --yes "$WRANGLER_PKG" "$@")
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

upload_r2_trajectories() {
  if [[ "$TRACER_R2_UPLOAD" != "1" ]]; then
    return 0
  fi
  if [[ -z "$TRACER_R2_BUCKET" ]]; then
    log "TRACER_R2_UPLOAD=1 but TRACER_R2_BUCKET is empty; skipping R2 upload"
    return 0
  fi
  if [[ ! -f "$R2_MANIFEST_SCRIPT" ]]; then
    log "R2 manifest script not found at $R2_MANIFEST_SCRIPT; skipping R2 upload"
    return 0
  fi

  trial_fact_files=()
  while IFS= read -r path; do
    trial_fact_files+=("$path")
  done < <(find "$PUBLIC_DIR/data" -maxdepth 1 -type f \
    \( -name 'trial_fact.jsonl' -o -name 'trial_fact.[0-9][0-9][0-9].jsonl' \) \
    -print 2>/dev/null | sort)
  if [[ "${#trial_fact_files[@]}" -eq 0 ]]; then
    log "trial_fact JSONL exports not found; skipping R2 upload"
    return 0
  fi

  manifest="/tmp/tracer_r2_manifest.$$.tsv"
  cursor=0
  if [[ "$TRACER_R2_UPLOAD_LIMIT" -gt 0 && -f "$TRACER_R2_UPLOAD_CURSOR_FILE" ]]; then
    cursor="$(tr -dc '0-9' <"$TRACER_R2_UPLOAD_CURSOR_FILE")"
    cursor="${cursor:-0}"
  fi
  python3 "$R2_MANIFEST_SCRIPT" "${trial_fact_files[@]}" \
    --offset "$cursor" --limit "$TRACER_R2_UPLOAD_LIMIT" >"$manifest"
  count="$(wc -l <"$manifest" | tr -d ' ')"
  if [[ "$count" == "0" ]]; then
    if [[ "$cursor" -gt 0 ]]; then
      mkdir -p "$(dirname "$TRACER_R2_UPLOAD_CURSOR_FILE")"
      printf '0\n' >"$TRACER_R2_UPLOAD_CURSOR_FILE"
      log "R2 upload cursor reached the end; reset to the first trajectory for the next sync"
    else
      log "no local trajectory JSON files found for R2 upload"
    fi
    rm -f "$manifest"
    return 0
  fi

  log "uploading $count trajectory JSON file(s) to R2 bucket $TRACER_R2_BUCKET"
  while IFS=$'\t' read -r r2_key local_path; do
    if [[ -z "$r2_key" || -z "$local_path" ]]; then
      continue
    fi
    wrangler r2 object put "$TRACER_R2_BUCKET/$r2_key" \
      --file "$local_path" \
      --content-type application/json >/tmp/tracer_r2_upload.log 2>&1 || {
        log "failed to upload $r2_key; see /tmp/tracer_r2_upload.log"
        rm -f "$manifest"
        return 1
      }
  done <"$manifest"
  rm -f "$manifest"
  if [[ "$TRACER_R2_UPLOAD_LIMIT" -gt 0 ]]; then
    mkdir -p "$(dirname "$TRACER_R2_UPLOAD_CURSOR_FILE")"
    printf '%s\n' "$((cursor + count))" >"$TRACER_R2_UPLOAD_CURSOR_FILE"
  fi
  log "R2 trajectory upload complete"
}

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

# Fall back to the tree-wide shared credentials (root config.yaml ->
# runtime_info.input.cloudflare) for anything $ENV_FILE did not provide. Values
# already exported above win, so a per-block env file still overrides the shared
# config for this block.
SHARED_CREDS="$BLOCK_DIR/../../scripts/shared_credentials.sh"
if [[ -f "$SHARED_CREDS" ]]; then
  CF_LEGACY_ENV_FILE="$ENV_FILE"
  # shellcheck disable=SC1090
  source "$SHARED_CREDS"
  load_shared_credentials "$BLOCK_DIR"
fi

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" || -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
  log "missing CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID — set them in $ENV_FILE, in the environment, or in the root config.yaml runtime_info.input.cloudflare"
  exit 1
fi
log "cloudflare credentials source: ${SHARED_CLOUDFLARE_SOURCE:-env-file}"
export CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID

mkdir -p "$PUBLIC_DIR"

LOCK_FILE="${LOCK_FILE:-/tmp/tracer_dashboard_sync.lock}"
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
  sample_args=(
    --sample-limit "$DASHBOARD_SAMPLE_LIMIT"
    --sample-preview-chars "$DASHBOARD_SAMPLE_PREVIEW_CHARS"
    --sample-message-limit "$DASHBOARD_SAMPLE_MESSAGE_LIMIT"
    --local-mode "$DASHBOARD_LOCAL_MODE"
    --harbor-jobs-dir "$DASHBOARD_HARBOR_JOBS_DIR"
    --max-trials-per-job "$DASHBOARD_MAX_TRIALS_PER_JOB"
    --max-quality-records-per-dataset "$DASHBOARD_MAX_QUALITY_RECORDS_PER_DATASET"
  )
  if [[ "$DASHBOARD_INCLUDE_SAMPLES" == "0" ]]; then
    sample_args=(--no-include-samples "${sample_args[@]}")
  fi
  if uv run --no-project --script "$DASHBOARD_SCRIPT" \
      --output-html "$PUBLIC_DIR/index.html" \
      --cache-file "$CACHE_FILE" \
      --refresh "$LOOP_SECONDS" \
      --force-full-scan \
      "${sample_args[@]}"; then
    log "generated $PUBLIC_DIR/index.html"
  else
    log "generation failed; will retry after $LOOP_SECONDS seconds"
    sleep "$LOOP_SECONDS"
    continue
  fi

  upload_r2_trajectories || log "R2 upload failed; deploying static dashboard anyway"

  if [[ "$PROJECT_READY" -eq 0 ]]; then
    log "ensuring Cloudflare Pages project $PROJECT_NAME exists"
    wrangler pages project create "$PROJECT_NAME" \
      --production-branch "$BRANCH_NAME" >/tmp/tracer_pages_project_create.log 2>&1 || true
    PROJECT_READY=1
  fi

  log "deploying dashboard to Cloudflare Pages project $PROJECT_NAME"
  if ! wrangler pages deploy "$PUBLIC_DIR" \
    --project-name "$PROJECT_NAME" \
    --branch "$BRANCH_NAME" \
    --commit-dirty=true \
    --commit-message "Update tracer progress dashboard $(date -u '+%Y-%m-%dT%H:%M:%SZ')"; then
    log "Cloudflare Pages deploy failed; will retry after $LOOP_SECONDS seconds"
    sleep "$LOOP_SECONDS"
    continue
  fi

  log "sleeping $LOOP_SECONDS seconds before next refresh"
  sleep "$LOOP_SECONDS"
done
