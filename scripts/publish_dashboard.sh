#!/usr/bin/env bash
# Publish a block's built dashboard and report the URL it is actually reachable at.
#
# Source this from a block script (safe under `set -euo pipefail`; it never exits
# the caller), then call:
#
#   source "$(git rev-parse --show-toplevel)/scripts/publish_dashboard.sh"
#   publish_dashboard <block> <dir> [port]
#
# Two modes, chosen by whether Cloudflare credentials resolve:
#
#   credentials present -> Cloudflare Pages, project `legoflow-<block>`.
#                          Persistent; survives this process exiting.
#   no credentials      -> cloudflared quick tunnel over a local static server.
#                          Ephemeral: a fresh *.trycloudflare.com name each run,
#                          alive only while this process is.
#
# Why the project name is hardcoded rather than configured: a Pages project name
# is an account-local identifier, and one stable name per block is what makes a
# re-deploy replace the previous board instead of piling up new ones. It is NOT
# the URL — see PUBLISHED_URL below.
#
# Exports after a successful call:
#   PUBLISHED_URL   the address to hand a human, or "" when publishing was skipped
#   PUBLISHED_MODE  pages | tunnel | none

PUBLISH_PRODUCTION_BRANCH="${PUBLISH_PRODUCTION_BRANCH:-main}"
# wrangler v4+ needs Node >= 22; v3 also runs on Node 18.
PUBLISH_WRANGLER_PKG="${PUBLISH_WRANGLER_PKG:-wrangler@3}"

_publish_log() { printf '[publish] %s\n' "$*" >&2; }

_publish_wrangler() {
  local workdir="${PUBLISH_WRANGLER_WORKDIR:-${TMPDIR:-/tmp}/legoflow-wrangler}"
  mkdir -p "$workdir"
  (cd "$workdir" && npx --yes "$PUBLISH_WRANGLER_PKG" "$@")
}

# The canonical hostname Cloudflare assigned this project. Never build it from the
# project name: `*.pages.dev` subdomains are globally unique, so a taken name is
# silently given a suffix (project `swe-databoard` answers on
# `swe-databoard-ems.pages.dev`). Guessing prints an address that 404s.
_publish_pages_url() {
  local project="$1" response
  response="$(curl -sS --max-time 25 \
    -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
    "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/pages/projects/$project" \
    2>/dev/null)" || return 1
  printf '%s' "$response" | python3 -c '
import json, sys
try:
    result = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
if not result.get("success"):
    sys.exit(1)
project = result.get("result") or {}
# `domains` leads with any custom domain the operator attached; fall back to the
# assigned subdomain.
for candidate in (project.get("domains") or []) + [project.get("subdomain") or ""]:
    if candidate:
        print(candidate if candidate.startswith("http") else "https://" + candidate)
        break
else:
    sys.exit(1)
' 2>/dev/null
}

_publish_to_pages() {
  local block="$1" dir="$2" project="legoflow-$block"

  _publish_log "deploying $dir to Cloudflare Pages project $project"
  # Idempotent: fails harmlessly when the project already exists.
  _publish_wrangler pages project create "$project" \
    --production-branch "$PUBLISH_PRODUCTION_BRANCH" >/dev/null 2>&1 || true

  if ! _publish_wrangler pages deploy "$dir" \
      --project-name "$project" \
      --branch "$PUBLISH_PRODUCTION_BRANCH" \
      --commit-dirty=true \
      --commit-message "$block dashboard $(date -u '+%Y-%m-%dT%H:%M:%SZ')"; then
    _publish_log "deploy failed"
    return 1
  fi

  PUBLISHED_URL="$(_publish_pages_url "$project" || true)"
  PUBLISHED_MODE="pages"
  if [[ -z "$PUBLISHED_URL" ]]; then
    # Deployed, but the address could not be read back. Say so rather than
    # printing a guess.
    _publish_log "deployed, but could not read the project URL back from the API"
    _publish_log "check the dashboard: Workers & Pages -> $project"
    return 0
  fi
  return 0
}

_publish_via_tunnel() {
  local dir="$1" port="${2:-8791}"
  local bin="${CLOUDFLARED_BIN:-$(command -v cloudflared 2>/dev/null || true)}"
  if [[ -z "$bin" ]]; then
    _publish_log "no Cloudflare credentials, and cloudflared is not installed —"
    _publish_log "nothing was published. Either set CLOUDFLARE_API_TOKEN and"
    _publish_log "CLOUDFLARE_ACCOUNT_ID (root config.yaml -> runtime_info.input.cloudflare),"
    _publish_log "or install cloudflared for a temporary public URL:"
    _publish_log "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    _publish_log "the board is still readable locally at $dir/index.html"
    PUBLISHED_URL=""; PUBLISHED_MODE="none"
    return 1
  fi

  python3 -m http.server "$port" --bind 127.0.0.1 --directory "$dir" >/dev/null 2>&1 &
  PUBLISH_SERVER_PID=$!
  sleep 1
  if ! kill -0 "$PUBLISH_SERVER_PID" 2>/dev/null; then
    _publish_log "could not serve $dir on port $port"
    PUBLISHED_URL=""; PUBLISHED_MODE="none"
    return 1
  fi

  local log="${TMPDIR:-/tmp}/legoflow-tunnel-$port.log"
  : > "$log"
  "$bin" tunnel --url "http://127.0.0.1:$port" >"$log" 2>&1 &
  PUBLISH_TUNNEL_PID=$!
  _publish_log "opening a quick tunnel to 127.0.0.1:$port (log: $log)"

  # cloudflared prints the assigned hostname once the edge accepts the tunnel.
  PUBLISHED_URL=""
  local i
  for i in $(seq 1 30); do
    PUBLISHED_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" 2>/dev/null | head -1)"
    [[ -n "$PUBLISHED_URL" ]] && break
    kill -0 "$PUBLISH_TUNNEL_PID" 2>/dev/null || break
    sleep 1
  done

  if [[ -z "$PUBLISHED_URL" ]]; then
    _publish_log "the tunnel did not report a URL within 30s; see $log"
    PUBLISHED_MODE="none"
    return 1
  fi
  PUBLISHED_MODE="tunnel"
  return 0
}

publish_dashboard() {
  local block="${1:?publish_dashboard <block> <dir> [port]}"
  local dir="${2:?publish_dashboard <block> <dir> [port]}"
  local port="${3:-8791}"
  PUBLISHED_URL=""; PUBLISHED_MODE="none"

  if [[ ! -d "$dir" ]]; then
    _publish_log "nothing to publish: $dir does not exist"
    return 1
  fi

  local shared
  shared="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/shared_credentials.sh"
  if [[ -f "$shared" ]] && ! declare -F load_shared_credentials >/dev/null; then
    # shellcheck disable=SC1090
    source "$shared"
  fi
  declare -F load_shared_credentials >/dev/null && load_shared_credentials "$dir"

  if [[ -n "${CLOUDFLARE_API_TOKEN:-}" && -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
    export CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID
    export CI="${CI:-1}"
    _publish_to_pages "$block" "$dir" || return 1
  else
    _publish_log "no Cloudflare credentials resolved — falling back to a temporary tunnel"
    _publish_via_tunnel "$dir" "$port" || return 1
  fi

  if [[ -n "$PUBLISHED_URL" ]]; then
    printf '\n  Public URL: %s\n' "$PUBLISHED_URL" >&2
    if [[ "$PUBLISHED_MODE" == "tunnel" ]]; then
      printf '  (temporary: a new address each run, and only while this process runs)\n\n' >&2
    else
      printf '\n' >&2
    fi
  fi
  return 0
}
