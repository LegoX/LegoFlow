#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${HARBOR_HOME:-$HOME}"
ENV_FILE="${ENV_FILE:-$HOME_DIR/.config/harbor_webui_cloudflare.env}"

# The Pages project is `legoflow-<block>`, decided by scripts/publish_dashboard.sh.
# It is an account-local identifier, not the URL: the URL is read back after the
# deploy, because Cloudflare suffixes a name whose subdomain is already taken.
BRANCH_NAME="${BRANCH_NAME:-harbor-webui}"
LOOP_SECONDS="${LOOP_SECONDS:-3600}"
PORT="${PORT:-8093}"
HARBOR_JOBS_DIR="${HARBOR_JOBS_DIR:-$SCRIPT_DIR/../artifacts/jobs}"
PUBLIC_DIR="${PUBLIC_DIR:-$SCRIPT_DIR/site}"
EXPORT_EXTRA_ARGS="${EXPORT_EXTRA_ARGS:-}"
TRAJECTORY_TARGET="${TRAJECTORY_TARGET:-chunks}"
TRAJECTORY_CHUNK_MB="${TRAJECTORY_CHUNK_MB:-8}"
TRIAL_DETAIL_TARGET="${TRIAL_DETAIL_TARGET:-chunks}"
TRIAL_DETAIL_CHUNK_MB="${TRIAL_DETAIL_CHUNK_MB:-8}"
R2_BUCKET_NAME="${R2_BUCKET_NAME:-}"
R2_UPLOAD_WORKERS="${R2_UPLOAD_WORKERS:-8}"
LOCK_FILE="${LOCK_FILE:-/tmp/harbor_webui_pages_sync.lock}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

if [[ -f "$ENV_FILE" ]]; then
  # Parse KEY=VALUE lines instead of `source`-ing the file, so a stray command
  # in the config can't execute with this script's privileges. Handles an
  # optional `export ` prefix and surrounding single/double quotes.
  while IFS='=' read -r _key _val || [[ -n "$_key" ]]; do
    _key="${_key#export }"
    _key="${_key// /}"
    [[ -z "$_key" || "$_key" == \#* ]] && continue
    [[ "$_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    _val="${_val%\"}"; _val="${_val#\"}"
    _val="${_val%\'}"; _val="${_val#\'}"
    printf -v "$_key" '%s' "$_val"
    export "${_key?}"
  done < "$ENV_FILE"
  unset _key _val
fi

# Fall back to the tree-wide shared credentials (root config.yaml ->
# runtime_info.input.cloudflare) for anything $ENV_FILE did not provide. Values
# exported above win, so a per-block env file still overrides the shared config.
PUBLISH_LIB="$SCRIPT_DIR/../../../scripts/publish_dashboard.sh"
if [[ -f "$PUBLISH_LIB" ]]; then
  # shellcheck disable=SC1090
  source "$PUBLISH_LIB"
fi
SHARED_CREDS="$SCRIPT_DIR/../../../scripts/shared_credentials.sh"
if [[ -f "$SHARED_CREDS" ]]; then
  CF_LEGACY_ENV_FILE="$ENV_FILE"
  # shellcheck disable=SC1090
  source "$SHARED_CREDS"
  load_shared_credentials "$SCRIPT_DIR"
fi

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" || -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
  log "missing CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID — set them in $ENV_FILE, in the environment, or in the root config.yaml runtime_info.input.cloudflare"
  exit 1
fi
log "cloudflare credentials source: ${SHARED_CLOUDFLARE_SOURCE:-env-file}"
case "$TRAJECTORY_TARGET" in
  none)
    if [[ -z "${R2_BUCKET_NAME:-}" ]]; then
      log "TRAJECTORY_TARGET=none requires R2_BUCKET_NAME"
      exit 1
    fi
    ;;
  chunks|pages)
    ;;
  *)
    log "unsupported TRAJECTORY_TARGET=$TRAJECTORY_TARGET (expected none, chunks, or pages)"
    exit 1
    ;;
esac
case "$TRIAL_DETAIL_TARGET" in
  files|chunks)
    ;;
  *)
    log "unsupported TRIAL_DETAIL_TARGET=$TRIAL_DETAIL_TARGET (expected files or chunks)"
    exit 1
    ;;
esac
export CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID R2_BUCKET_NAME R2_UPLOAD_WORKERS TRAJECTORY_TARGET TRAJECTORY_CHUNK_MB TRIAL_DETAIL_TARGET TRIAL_DETAIL_CHUNK_MB

mkdir -p "$PUBLIC_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another Harbor WebUI Pages sync is already running; exit"
  exit 1
fi

log "starting local static preview at http://127.0.0.1:$PORT/index.html"
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$PUBLIC_DIR" >/dev/null 2>&1 &
SERVER_PID=$!
sleep 1
if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
  log "failed to start local static preview on port $PORT"
  exit 1
fi

cleanup() {
  kill "$SERVER_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM

PROJECT_READY=0

while true; do
  log "exporting Harbor dashboard from $HARBOR_JOBS_DIR to $PUBLIC_DIR with trajectory target $TRAJECTORY_TARGET"
  # shellcheck disable=SC2086
  if python3 "$SCRIPT_DIR/export_static.py" \
      --jobs-dir "$HARBOR_JOBS_DIR" \
      --output-dir "$PUBLIC_DIR" \
      --trajectory-target "$TRAJECTORY_TARGET" \
      --trajectory-chunk-mb "$TRAJECTORY_CHUNK_MB" \
      --trial-detail-target "$TRIAL_DETAIL_TARGET" \
      --trial-detail-chunk-mb "$TRIAL_DETAIL_CHUNK_MB" \
      $EXPORT_EXTRA_ARGS; then
    log "exported fast static site to $PUBLIC_DIR"
  else
    log "static export failed; will retry after $LOOP_SECONDS seconds"
    sleep "$LOOP_SECONDS"
    continue
  fi

  if [[ "$TRAJECTORY_TARGET" == "none" ]]; then
    log "ensuring R2 bucket $R2_BUCKET_NAME exists"
    npx --yes wrangler r2 bucket create "$R2_BUCKET_NAME" >/tmp/harbor_webui_r2_bucket_create.log 2>&1 || true
    log "uploading trajectories to R2 bucket $R2_BUCKET_NAME"
    if python3 "$SCRIPT_DIR/upload_trajectories_r2.py" \
        --jobs-dir "$HARBOR_JOBS_DIR" \
        --bucket "$R2_BUCKET_NAME" \
        --workers "$R2_UPLOAD_WORKERS"; then
      log "uploaded trajectories to R2"
    else
      log "trajectory upload failed; will retry after $LOOP_SECONDS seconds"
      sleep "$LOOP_SECONDS"
      continue
    fi
  elif [[ "$TRAJECTORY_TARGET" == "chunks" ]]; then
    log "using static trajectory chunks; R2 upload skipped"
  elif [[ "$TRAJECTORY_TARGET" == "pages" ]]; then
    log "trajectories are embedded in the Pages export; R2 upload skipped"
  fi

  # Publishes to Cloudflare Pages when credentials resolve, or over a temporary
  # cloudflared tunnel when they do not, and reports the address either way.
  publish_dashboard evaluator "$PUBLIC_DIR" "${PORT:-8792}" || \
    log "publish failed; retrying on the next pass"

  log "sleeping $LOOP_SECONDS seconds before next refresh"
  sleep "$LOOP_SECONDS"
done
