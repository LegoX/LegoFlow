#!/usr/bin/env bash
# Publish the trainer dashboard to Cloudflare Pages as a static snapshot.
#
# The board normally runs as a live server (start_dashboard.sh -> server.py),
# which reads artifacts/model/ on every request. Pages has no such disk, so this
# script runs export_static.py first to snapshot the same payloads into site/,
# then hands that directory to scripts/publish_dashboard.sh.
#
# The Pages project is `legoflow-<block>`, decided by scripts/publish_dashboard.sh.
# It is an account-local identifier, not the URL: the URL is read back after the
# deploy, because Cloudflare suffixes a name whose subdomain is already taken.
#
# Trade-off vs. start_dashboard.sh: a snapshot has no live refresh, no AI report
# generation, and no wandb proxy. Use TUNNEL=true ./start_dashboard.sh while a
# run is in flight; use this to leave a durable link behind afterwards.
#
#   bash run_cloudflare_pages_sync.sh              # publish once and exit
#   LOOP_SECONDS=3600 bash run_cloudflare_pages_sync.sh --loop
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${HARBOR_HOME:-$HOME}"
ENV_FILE="${ENV_FILE:-$HOME_DIR/.config/legoflow-trainer_cloudflare.env}"

LOOP_SECONDS="${LOOP_SECONDS:-3600}"
PORT="${PORT:-8094}"
SAVE_DIR="${SAVE_DIR:-$SCRIPT_DIR/../artifacts/model}"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/../artifacts/logs}"
DIST_DIR="${DIST_DIR:-$SCRIPT_DIR/dist}"
PUBLIC_DIR="${PUBLIC_DIR:-$SCRIPT_DIR/site}"
LOG_TAIL="${LOG_TAIL:-2000}"
LOCK_FILE="${LOCK_FILE:-/tmp/legoflow_trainer_pages_sync.lock}"

LOOP=0
[[ "${1:-}" == "--loop" ]] && LOOP=1

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

if [[ -f "$ENV_FILE" ]]; then
  # Parse KEY=VALUE lines instead of `source`-ing the file, so a stray command
  # in the config can't execute with this script's privileges.
  while IFS='=' read -r _key _val || [[ -n "$_key" ]]; do
    _key="${_key#export }"
    _key="${_key// /}"
    [[ -z "$_key" || "$_key" == \#* ]] && continue
    [[ "$_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    # PATH here would be a literal, unexpanded string and would break python3/npx.
    [[ "$_key" == "PATH" ]] && continue
    _val="${_val%\"}"; _val="${_val#\"}"
    _val="${_val%\'}"; _val="${_val#\'}"
    printf -v "$_key" '%s' "$_val"
    export "${_key?}"
  done < "$ENV_FILE"
  unset _key _val
fi

# Fall back to the tree-wide shared credentials (root config.yaml ->
# runtime_info.input.cloudflare) for anything $ENV_FILE did not provide.
PUBLISH_LIB="$SCRIPT_DIR/../../../scripts/publish_dashboard.sh"
if [[ -f "$PUBLISH_LIB" ]]; then
  # shellcheck disable=SC1090
  source "$PUBLISH_LIB"
else
  log "missing $PUBLISH_LIB — cannot publish"
  exit 1
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
  log "note: a tmux session started from a stale server env will not see a token exported by ~/.bashrc; start it with 'bash -ic' or set \$ENV_FILE"
  exit 1
fi
log "cloudflare credentials source: ${SHARED_CLOUDFLARE_SOURCE:-env-file}"
export CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID

if [[ ! -f "$DIST_DIR/index.html" ]]; then
  log "building frontend (no $DIST_DIR/index.html)"
  (cd "$SCRIPT_DIR" && npm install && npm run build) || { log "frontend build failed"; exit 1; }
fi

mkdir -p "$PUBLIC_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another trainer Pages sync is already running; exit"
  exit 1
fi

while true; do
  log "exporting trainer dashboard from $SAVE_DIR to $PUBLIC_DIR"
  if python3 "$SCRIPT_DIR/export_static.py" \
      --output-dir "$PUBLIC_DIR" \
      --dist-dir "$DIST_DIR" \
      --save-dir "$SAVE_DIR" \
      --log-dir "$LOG_DIR" \
      --log-tail "$LOG_TAIL"; then
    log "exported static site to $PUBLIC_DIR"
  else
    log "static export failed"
    [[ "$LOOP" -eq 1 ]] || exit 1
    sleep "$LOOP_SECONDS"
    continue
  fi

  # Publishes to Cloudflare Pages when credentials resolve, or over a temporary
  # cloudflared tunnel when they do not, and reports the address either way.
  publish_dashboard trainer "$PUBLIC_DIR" "$PORT" || \
    log "publish failed"

  if [[ "$LOOP" -eq 0 ]]; then
    break
  fi
  log "sleeping $LOOP_SECONDS seconds before next refresh"
  sleep "$LOOP_SECONDS"
done
