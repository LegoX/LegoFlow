#!/usr/bin/env bash
# Validate the root legoflow block without side effects.
# Config/schema/dependency validation is delegated to scripts/validate_config.py
# (the shared block-contract validator). Pass --full to also run each block's
# own dryrun.sh (locally, or on the remote node when resources.ip is remote).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS=0
FAIL=0
WARN=0
FULL=0

ok()   { echo "  [OK]   $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $1"; WARN=$((WARN+1)); }
info() { echo "  [INFO] $1"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) FULL=1; shift ;;
    -h|--help)
      echo "Usage: bash scripts/dryrun.sh [--full]"
      echo "  --full   also run each block's dryrun.sh"
      exit 0
      ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

cfg() {
  python3 - "$1" "$2" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("")
    sys.exit(0)
config_path, dotted_key = sys.argv[1], sys.argv[2]
with open(config_path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}
value = data
for part in dotted_key.split("."):
    if not isinstance(value, dict):
        value = None
        break
    value = value.get(part)
if value is None:
    print("")
else:
    print(str(value))
PY
}

ROOT_CFG="$ROOT_DIR/config.yaml"

echo "=== Block Dryrun: legoflow ==="
echo ""

# ── 1. Local file checks ──────────────────────────────────────────────────────
echo "1. Local files"

for f in CLAUDE.md config.yaml \
         .claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md \
         scripts/validate_config.py; do
  if [[ -f "$ROOT_DIR/$f" ]]; then
    ok "$f"
  else
    fail "missing: $f"
  fi
done

# artifacts/index.yaml is runtime state written by scripts/archive_run.sh (the
# start.sh EXIT trap), not a precondition — and it is gitignored, so a fresh
# clone legitimately has none. Absence is INFO, never a failure.
if [[ -f "$ROOT_DIR/artifacts/index.yaml" ]]; then
  ok "artifacts/index.yaml"
else
  info "artifacts/index.yaml absent (auto-created by archive_run.sh after first run)"
fi

for d in artifacts scripts blocks; do
  if [[ -d "$ROOT_DIR/$d" ]]; then
    ok "dir: $d/"
  else
    fail "missing dir: $d/"
  fi
done

# ── 2. Config validation (root + every block + cross-block deps) ──────────
echo ""
echo "2. Config validation (scripts/validate_config.py --root)"

VALIDATOR_OUT="$(python3 "$ROOT_DIR/scripts/validate_config.py" --root "$ROOT_DIR" 2>&1)" && VALIDATOR_RC=0 || VALIDATOR_RC=$?
echo "$VALIDATOR_OUT" | sed 's/^/    /'
V_FAIL="$(echo "$VALIDATOR_OUT" | grep -c '^\[FAIL\]' || true)"
V_WARN="$(echo "$VALIDATOR_OUT" | grep -c '^\[WARN\]' || true)"
if [[ $VALIDATOR_RC -eq 0 ]]; then
  ok "validate_config: no failures (${V_WARN} warnings)"
else
  fail "validate_config: ${V_FAIL} failures (${V_WARN} warnings) — see lines above"
fi
WARN=$((WARN+V_WARN))

# ── 3. Deployment & registry credentials ─────────────────────────────────────
echo ""
echo "3. Deployment & registry credentials"

# These are the tree-wide, OPTIONAL credentials declared in root config.yaml ->
# runtime_info.input.{cloudflare,docker}. scripts/shared_credentials.sh resolves
# them as env > root config.yaml > legacy per-block env file, and every block
# reads them through the same helper, so what is reported here is exactly what
# the blocks will see. Absence is always a WARN, never a FAIL.
CF_ENV_FILE="${ENV_FILE:-$HOME/.config/trajgen_progress_cloudflare.env}"
if [[ -f "$ROOT_DIR/scripts/shared_credentials.sh" ]]; then
  CF_LEGACY_ENV_FILE="$CF_ENV_FILE"
  # shellcheck source=/dev/null
  source "$ROOT_DIR/scripts/shared_credentials.sh"
  load_shared_credentials "$ROOT_DIR"
else
  fail "scripts/shared_credentials.sh missing — blocks cannot resolve shared cloudflare/docker credentials"
fi

if [[ -n "${CLOUDFLARE_API_TOKEN:-}" && -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
  ok "cloudflare: account_id + api_token resolved from ${SHARED_CLOUDFLARE_SOURCE}"
  # Availability, not just presence: verify the token against the Cloudflare API.
  CF_PROBE="$(shared_cloudflare_probe)" && ok "cloudflare: $CF_PROBE" \
    || warn "cloudflare: $CF_PROBE — deploys will fail until the token is fixed"
  [[ -n "${CLOUDFLARE_PAGES_PROJECT_PREFIX:-}" ]] && \
    info "cloudflare: Pages project prefix '${CLOUDFLARE_PAGES_PROJECT_PREFIX}'"
elif [[ -n "${CLOUDFLARE_ACCOUNT_ID:-}" || -n "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  warn "cloudflare: only one of account_id/api_token is set (from ${SHARED_CLOUDFLARE_SOURCE}) — both are required; deploys will fail"
else
  warn "cloudflare: not configured (optional) — set runtime_info.input.cloudflare in root config.yaml, or export CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID. Without it docs/deploy_cloudflare_pages.sh and every block's dashboard publish are unavailable; local dashboards still work."
fi

if [[ -n "${DOCKER_USERNAME:-}" && -n "${DOCKER_PASSWORD:-}" ]]; then
  ok "docker registry: credentials resolved from ${SHARED_DOCKER_SOURCE} for ${DOCKER_REGISTRY:-docker.io}"
elif [[ -n "${DOCKER_USERNAME:-}" || -n "${DOCKER_PASSWORD:-}" ]]; then
  warn "docker registry: only one of username/password is set (from ${SHARED_DOCKER_SOURCE}) — both are required"
else
  info "docker registry: no credentials in root config.yaml runtime_info.input.docker (optional) — falling back to any existing docker login below"
fi
[[ -n "${DOCKER_MIRROR:-}" ]] && info "docker registry: pull-through mirror configured (${DOCKER_MIRROR})"

# Docker Hub login — anonymous pulls are limited to 100 per 6h per IP;
# tracer/evaluator pull task + agent-runtime images and can hit the limit
# mid-job (manifest errors that surface as agent/verifier failures).
DOCKER_CFG="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
if ! command -v docker >/dev/null 2>&1; then
  warn "docker: CLI not found — skipping registry-auth check (required by curator/tracer/evaluator)"
elif [[ -f "$DOCKER_CFG" ]] && python3 - "$DOCKER_CFG" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
auths = cfg.get("auths") or {}
if any("docker.io" in k for k in auths if (auths.get(k) or {}).get("auth")):
    sys.exit(0)
if cfg.get("credsStore") or any("docker.io" in k for k in (cfg.get("credHelpers") or {})):
    sys.exit(0)
sys.exit(1)
PY
then
  ok "docker: Docker Hub login found in $DOCKER_CFG"
else
  warn "docker: no Docker Hub login in $DOCKER_CFG — anonymous pulls are capped at 100/6h per IP; run \`bash scripts/docker_login.sh\` (uses the credentials above) or \`docker login\` to avoid mid-job pull failures in tracer/evaluator"
fi

# ── 4. SSH reachability ───────────────────────────────────────────────────────
echo ""
echo "4. SSH reachability"

REMOTE_IP="$(cfg "$ROOT_CFG" "meta_info.resources.ip" 2>/dev/null || echo "")"
REMOTE_USER="$(cfg "$ROOT_CFG" "meta_info.resources.user" 2>/dev/null || echo "root")"
REMOTE_DIR="$(cfg "$ROOT_CFG" "meta_info.resources.directory" 2>/dev/null || echo "")"
[[ -z "$REMOTE_USER" || "$REMOTE_USER" == "null" ]] && REMOTE_USER="root"

SSH_OK=0
if [[ -z "$REMOTE_IP" || "$REMOTE_IP" == "null" || "$REMOTE_IP" == "local" ]]; then
  ok "local execution (ip=${REMOTE_IP:-<unset>}) — SSH check not required"
else
  info "testing SSH to ${REMOTE_USER}@${REMOTE_IP} ..."
  if ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no \
       "${REMOTE_USER}@${REMOTE_IP}" echo ok &>/dev/null; then
    ok "SSH to ${REMOTE_USER}@${REMOTE_IP}"
    SSH_OK=1
  else
    warn "SSH to ${REMOTE_USER}@${REMOTE_IP} failed — node may be unreachable from this machine"
  fi

  if [[ $SSH_OK -eq 1 && -n "$REMOTE_DIR" && "$REMOTE_DIR" != "null" ]]; then
    REMOTE_REPO_DIR="${REMOTE_DIR%/}/SWE-Lego-Live"
    if ssh -o BatchMode=yes "${REMOTE_USER}@${REMOTE_IP}" \
         "test -d '${REMOTE_REPO_DIR}'" 2>/dev/null; then
      ok "remote dir exists: ${REMOTE_REPO_DIR}"
    else
      warn "remote dir not found: ${REMOTE_REPO_DIR} — run scripts/start.sh to sync"
    fi
  fi
fi

# ── 5. Full block dryruns (optional) ───────────────────────────────────────
if [[ $FULL -eq 1 ]]; then
  echo ""
  echo "5. Block dryruns (--full)"

  BLOCKS="$(python3 - "$ROOT_CFG" <<'PY'
import sys, yaml
with open(sys.argv[1], encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh) or {}
print(" ".join((cfg.get("meta_info") or {}).get("blocks") or {}))
PY
)"

  for block in $BLOCKS; do
    if [[ ! -f "$ROOT_DIR/blocks/${block}/scripts/dryrun.sh" ]]; then
      warn "blocks/${block} has no scripts/dryrun.sh"
      continue
    fi
    if [[ $SSH_OK -eq 1 ]]; then
      REMOTE_REPO_DIR="${REMOTE_DIR%/}/SWE-Lego-Live"
      info "running blocks/${block}/scripts/dryrun.sh on remote ..."
      if ssh -o BatchMode=yes "${REMOTE_USER}@${REMOTE_IP}" \
           "cd '${REMOTE_REPO_DIR}/blocks/${block}' && bash scripts/dryrun.sh" 2>&1 \
           | sed "s/^/    [${block}] /"; then
        ok "blocks/${block} dryrun passed"
      else
        fail "blocks/${block} dryrun failed"
      fi
    else
      info "running blocks/${block}/scripts/dryrun.sh locally ..."
      if (cd "$ROOT_DIR/blocks/${block}" && bash scripts/dryrun.sh) 2>&1 \
           | sed "s/^/    [${block}] /"; then
        ok "blocks/${block} dryrun passed"
      else
        fail "blocks/${block} dryrun failed"
      fi
    fi
  done
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Summary: ${PASS} passed, ${WARN} warnings, ${FAIL} failed ==="

if [[ $FAIL -gt 0 ]]; then
  echo "RESULT: FAIL"
  exit 1
elif [[ $WARN -gt 0 ]]; then
  echo "RESULT: PASS (with warnings)"
else
  echo "RESULT: PASS"
fi
