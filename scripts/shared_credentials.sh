#!/usr/bin/env bash
# Shared, OPTIONAL infrastructure credentials for every block in the tree.
#
# Source this from any block script (it is safe under `set -euo pipefail` and
# never exits the caller):
#
#   source "$(git rev-parse --show-toplevel)/scripts/shared_credentials.sh"
#   load_shared_credentials
#
# Resolution order per field, first non-empty wins:
#   1. an already-exported environment variable  (source: env)
#   2. root config.yaml -> runtime_info.input.{cloudflare,docker}  (source: root-config)
#   3. the block's own legacy env file, if the caller sets CF_LEGACY_ENV_FILE
#      before calling                                            (source: legacy-file)
#
# Env always wins so a one-off `CLOUDFLARE_API_TOKEN=... bash …` still overrides
# the committed config. Nothing here is required: every field may stay empty, and
# callers must treat "unset" as "feature disabled", not as an error.
#
# Exports:
#   CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN CLOUDFLARE_PAGES_PROJECT_PREFIX
#   DOCKER_REGISTRY DOCKER_USERNAME DOCKER_PASSWORD DOCKER_MIRROR DOCKER_HOST
#   SHARED_CLOUDFLARE_SOURCE SHARED_DOCKER_SOURCE   (env|root-config|legacy-file|unset)
#   SHARED_ROOT_CONFIG                              (path actually used, or "")

# Walk up from a starting directory to the tree root (the dir holding both
# config.yaml and subblock/). Falls back to git toplevel.
_shared_find_root() {
  local dir="${1:-$PWD}"
  dir="$(cd "$dir" 2>/dev/null && pwd)" || return 1
  while [[ "$dir" != "/" ]]; do
    if [[ -f "$dir/config.yaml" && -d "$dir/subblock" ]]; then
      printf '%s\n' "$dir"
      return 0
    fi
    dir="$(dirname "$dir")"
  done
  git -C "${1:-$PWD}" rev-parse --show-toplevel 2>/dev/null && return 0
  return 1
}

# Read one dotted key out of the root config's runtime_info.input.
# Prints the empty string when PyYAML is missing or the key is absent, so a
# machine without PyYAML degrades to "env only" instead of breaking the caller.
_shared_cfg_get() {
  local cfg="$1" dotted="$2"
  [[ -f "$cfg" ]] || { printf '\n'; return 0; }
  python3 - "$cfg" "$dotted" <<'PY' 2>/dev/null || printf '\n'
import sys
try:
    import yaml
except ImportError:
    print("")
    sys.exit(0)
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
except Exception:
    print("")
    sys.exit(0)
node = ((cfg.get("runtime_info") or {}).get("input") or {})
for part in sys.argv[2].split("."):
    if not isinstance(node, dict):
        node = None
        break
    node = node.get(part)
print("" if node is None else str(node))
PY
}

# Pull KEY=value out of a legacy per-block env file without sourcing it.
_shared_envfile_get() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || { printf '\n'; return 0; }
  # \042 = double quote, \047 = single quote — strip either style of wrapping.
  grep -E "^(export )?${key}=" "$file" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\042\047' || printf '\n'
}

load_shared_credentials() {
  local root cfg
  root="$(_shared_find_root "${1:-$PWD}")" || root=""
  cfg="${root:+$root/config.yaml}"
  export SHARED_ROOT_CONFIG="${cfg:-}"

  local legacy="${CF_LEGACY_ENV_FILE:-}"

  # ── Cloudflare ────────────────────────────────────────────────────────────
  local cf_src="unset"
  if [[ -n "${CLOUDFLARE_API_TOKEN:-}" || -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
    cf_src="env"
  fi
  local v
  if [[ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
    v="$(_shared_cfg_get "$cfg" "cloudflare.account_id")"
    if [[ -n "$v" ]]; then
      CLOUDFLARE_ACCOUNT_ID="$v"; [[ "$cf_src" == "unset" ]] && cf_src="root-config"
    elif [[ -n "$legacy" ]]; then
      v="$(_shared_envfile_get "$legacy" CLOUDFLARE_ACCOUNT_ID)"
      [[ -n "$v" ]] && { CLOUDFLARE_ACCOUNT_ID="$v"; [[ "$cf_src" == "unset" ]] && cf_src="legacy-file"; }
    fi
  fi
  if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
    v="$(_shared_cfg_get "$cfg" "cloudflare.api_token")"
    if [[ -n "$v" ]]; then
      CLOUDFLARE_API_TOKEN="$v"; [[ "$cf_src" == "unset" ]] && cf_src="root-config"
    elif [[ -n "$legacy" ]]; then
      v="$(_shared_envfile_get "$legacy" CLOUDFLARE_API_TOKEN)"
      [[ -n "$v" ]] && { CLOUDFLARE_API_TOKEN="$v"; [[ "$cf_src" == "unset" ]] && cf_src="legacy-file"; }
    fi
  fi
  if [[ -z "${CLOUDFLARE_PAGES_PROJECT_PREFIX:-}" ]]; then
    CLOUDFLARE_PAGES_PROJECT_PREFIX="$(_shared_cfg_get "$cfg" "cloudflare.pages_project_prefix")"
  fi
  export CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-}"
  export CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
  export CLOUDFLARE_PAGES_PROJECT_PREFIX="${CLOUDFLARE_PAGES_PROJECT_PREFIX:-}"
  export SHARED_CLOUDFLARE_SOURCE="$cf_src"

  # ── Docker registry ───────────────────────────────────────────────────────
  # Purpose is pull throughput, not deployment: an authenticated Docker Hub pull
  # lifts the anonymous 100-per-6h-per-IP cap that otherwise surfaces mid-job as
  # manifest errors in curator/tracer/evaluator.
  local dk_src="unset"
  [[ -n "${DOCKER_USERNAME:-}" || -n "${DOCKER_PASSWORD:-}" ]] && dk_src="env"
  local k
  for k in registry username password mirror host; do
    local upper="DOCKER_${k^^}"
    if [[ -z "${!upper:-}" ]]; then
      v="$(_shared_cfg_get "$cfg" "docker.$k")"
      if [[ -n "$v" ]]; then
        printf -v "$upper" '%s' "$v"
        [[ "$dk_src" == "unset" && ( "$k" == "username" || "$k" == "password" ) ]] && dk_src="root-config"
      fi
    fi
  done
  export DOCKER_REGISTRY="${DOCKER_REGISTRY:-}"
  export DOCKER_USERNAME="${DOCKER_USERNAME:-}"
  export DOCKER_PASSWORD="${DOCKER_PASSWORD:-}"
  export DOCKER_MIRROR="${DOCKER_MIRROR:-}"
  export DOCKER_HOST="${DOCKER_HOST:-}"
  export SHARED_DOCKER_SOURCE="$dk_src"

  return 0
}

# Human-readable, SECRET-FREE status lines for dryrun/check output.
# Prints two lines: "cloudflare: <status>" and "docker: <status>".
# Never echoes a token or password — only whether one resolved and from where.
shared_credentials_status() {
  local cf docker
  if [[ -n "${CLOUDFLARE_API_TOKEN:-}" && -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
    cf="configured (account_id + api_token from ${SHARED_CLOUDFLARE_SOURCE})"
  elif [[ -n "${CLOUDFLARE_ACCOUNT_ID:-}" || -n "${CLOUDFLARE_API_TOKEN:-}" ]]; then
    local have=(); [[ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]] && have+=(account_id)
    [[ -n "${CLOUDFLARE_API_TOKEN:-}" ]] && have+=(api_token)
    cf="INCOMPLETE (only ${have[*]} set, from ${SHARED_CLOUDFLARE_SOURCE}) — both are required"
  else
    cf="not configured (optional)"
  fi
  if [[ -n "${DOCKER_USERNAME:-}" && -n "${DOCKER_PASSWORD:-}" ]]; then
    docker="configured (username from ${SHARED_DOCKER_SOURCE})"
  elif [[ -n "${DOCKER_USERNAME:-}" || -n "${DOCKER_PASSWORD:-}" ]]; then
    docker="INCOMPLETE (username/password must both be set, from ${SHARED_DOCKER_SOURCE})"
  else
    docker="not configured (optional)"
  fi
  printf 'cloudflare: %s\n' "$cf"
  printf 'docker: %s\n' "$docker"
}

# Live probe: can this token actually deploy Pages to this account? Returns 0/1
# and prints a one-line reason. Requires curl. Read-only (lists Pages projects).
#
# Deliberately probes GET /accounts/<id>/pages/projects rather than
# /user/tokens/verify: the latter only accepts *user*-owned tokens and reports a
# perfectly good account-owned token as "Invalid API Token" (code 1000). Listing
# projects exercises exactly the capability the dashboards need — the token, the
# account id, and Pages permission — in one call, so it has no such blind spot.
shared_cloudflare_probe() {
  if [[ -z "${CLOUDFLARE_API_TOKEN:-}" || -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
    echo "skipped (not configured)"; return 1
  fi
  command -v curl >/dev/null 2>&1 || { echo "skipped (curl not found)"; return 1; }
  local body
  body="$(curl -sS -m 15 -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/pages/projects" 2>/dev/null)" || {
    echo "unreachable (network error)"; return 1; }
  # Parse as JSON rather than grepping: the API returns pretty-printed JSON
  # (`"success": true`, with a space), so a `"success":true` substring match
  # silently never fires and reports every valid token as rejected.
  local verdict
  verdict="$(printf '%s' "$body" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("BAD|response was not JSON"); raise SystemExit
if d.get("success"):
    r = d.get("result")
    print("OK|%s" % (len(r) if isinstance(r, list) else "?"))
else:
    errs = d.get("errors") or []
    codes = {e.get("code") for e in errs if isinstance(e, dict)}
    msg = "; ".join(str(e.get("message")) for e in errs if isinstance(e, dict)) or "unknown error"
    kind = "AUTH" if codes & {1000, 10000, 9109} else "PERM"
    print("%s|%s" % (kind, msg))' 2>/dev/null || echo "BAD|could not parse response")"

  case "${verdict%%|*}" in
    OK)   echo "token valid — Pages access confirmed (${verdict#*|} existing project(s))"; return 0 ;;
    AUTH) echo "token or account_id rejected by Cloudflare (${verdict#*|}) — check both values" ;;
    PERM) echo "authenticated but the Pages list call failed (${verdict#*|}) — token likely lacks Pages:Edit" ;;
    *)    echo "unexpected Cloudflare response (${verdict#*|})" ;;
  esac
  return 1
}
