#!/usr/bin/env bash
# CI test 08: agent.runtime_host_path is populated with the per-agent marker.
# eval bind-mounts this host dir read-only into every task container at
# container_runtime_root. If it is missing/empty the mount silently succeeds
# but the agent falls back to an in-container install (curl claude.ai/install.sh
# for claude-code, pip for openhands-sdk) that 403s/times out on isolated
# networks → a wall of exception.txt and zero usable trajectories. Mirrors
# dryrun.sh section 8's runtime-host-path check.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

cfg() { python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

AGENT_NAME="$(cfg runtime_info.input.agent.name)"
RUNTIME_HOST_PATH_RAW="$(cfg runtime_info.input.agent.runtime_host_path)"

[[ -n "$AGENT_NAME" ]] || { echo "FAIL: agent.name is empty"; exit 1; }
[[ -n "$RUNTIME_HOST_PATH_RAW" ]] || { echo "FAIL: agent.runtime_host_path is empty (bind-mount source required)"; exit 1; }

case "$AGENT_NAME" in
  custom-claude-code)   MARKER="bin/claude"; EXECUTABLE="bin/claude" ;;
  custom-openhands-sdk) MARKER="runtime-env.sh"; EXECUTABLE="bin/python" ;;
  custom-opencode)      MARKER="bin/opencode"; EXECUTABLE="bin/opencode" ;;
  *)                    MARKER=""; EXECUTABLE="" ;;
esac

if [[ "$RUNTIME_HOST_PATH_RAW" = /* ]]; then ABS="$RUNTIME_HOST_PATH_RAW"; else ABS="$BLOCK_DIR/$RUNTIME_HOST_PATH_RAW"; fi

if [[ ! -d "$ABS" ]]; then
  echo "FAIL: agent.runtime_host_path does not exist: $RUNTIME_HOST_PATH_RAW (re-extract via /eval:setup)"; exit 1
fi
if [[ -z "$(ls -A "$ABS" 2>/dev/null)" ]]; then
  echo "FAIL: agent.runtime_host_path is empty: $RUNTIME_HOST_PATH_RAW (re-extract via /eval:setup)"; exit 1
fi
if [[ -z "$MARKER" ]]; then
  echo "SKIP: no marker defined for agent.name=$AGENT_NAME; cannot verify runtime contents"; exit 77
fi
if [[ ! -e "$ABS/$MARKER" ]]; then
  echo "FAIL: agent.runtime_host_path missing marker $MARKER: $RUNTIME_HOST_PATH_RAW (re-extract via /eval:setup)"; exit 1
fi
if [[ -n "$EXECUTABLE" && ! -x "$ABS/$EXECUTABLE" ]]; then
  echo "FAIL: agent.runtime_host_path executable missing or not executable: $EXECUTABLE"; exit 1
fi
echo "PASS: agent.runtime_host_path populated for $AGENT_NAME ($MARKER present, $EXECUTABLE executable)"
