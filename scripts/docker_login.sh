#!/usr/bin/env bash
# Log in to the shared container registry so image pulls are authenticated.
#
# Why blocks call this: anonymous Docker Hub pulls are capped at 100 per 6h per
# IP. curator/tracer/evaluator each pull task images and agent runtimes; hitting
# the cap surfaces as a manifest error *mid-job*, which reads like an agent or
# verifier failure. An authenticated pull raises the cap substantially.
#
# Credentials come from root config.yaml -> runtime_info.input.docker (or the
# matching DOCKER_* env vars) via scripts/shared_credentials.sh.
#
# Usage:
#   bash scripts/docker_login.sh            # log in if configured, else no-op
#   bash scripts/docker_login.sh --status   # report only, never touch the daemon
#
# Exit codes: 0 = logged in, or nothing configured (a no-op is success — this is
# an optional accelerator and must never break a caller). 1 = configured but the
# login failed, which is worth surfacing.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT_DIR/scripts/shared_credentials.sh"
load_shared_credentials "$ROOT_DIR"

STATUS_ONLY=0
[[ "${1:-}" == "--status" ]] && STATUS_ONLY=1

REGISTRY="${DOCKER_REGISTRY:-docker.io}"

if [[ -z "${DOCKER_USERNAME:-}" || -z "${DOCKER_PASSWORD:-}" ]]; then
  echo "docker_login: no registry credentials configured (optional) — pulls stay anonymous"
  [[ -n "${DOCKER_USERNAME:-}${DOCKER_PASSWORD:-}" ]] && \
    echo "docker_login: WARNING one of username/password is set without the other; both are required"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker_login: docker CLI not found — cannot authenticate"
  exit 1
fi

# Already authenticated for this registry? Re-login is harmless but noisy, and
# it rewrites ~/.docker/config.json on every dryrun otherwise.
DOCKER_CFG="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
if [[ -f "$DOCKER_CFG" ]] && python3 - "$DOCKER_CFG" "$REGISTRY" <<'PY'
import json, sys
try:
    cfg = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
registry = sys.argv[2]
auths = cfg.get("auths") or {}
if any(registry in k for k in auths if (auths.get(k) or {}).get("auth")):
    sys.exit(0)
if cfg.get("credsStore") or any(registry in k for k in (cfg.get("credHelpers") or {})):
    sys.exit(0)
sys.exit(1)
PY
then
  echo "docker_login: already authenticated to $REGISTRY (per $DOCKER_CFG)"
  exit 0
fi

if [[ $STATUS_ONLY -eq 1 ]]; then
  echo "docker_login: credentials configured for $REGISTRY but not yet logged in (run without --status)"
  exit 0
fi

# --password-stdin keeps the secret out of the process table and shell history.
if printf '%s' "$DOCKER_PASSWORD" | docker login "$REGISTRY" \
     --username "$DOCKER_USERNAME" --password-stdin >/dev/null 2>&1; then
  echo "docker_login: authenticated to $REGISTRY as $DOCKER_USERNAME"
  exit 0
fi

echo "docker_login: login to $REGISTRY as $DOCKER_USERNAME FAILED (bad credentials, or registry unreachable)"
exit 1
