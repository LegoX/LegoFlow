#!/bin/bash
# Block entrypoint — edit config.yaml first, then run this.
#
# On first run (no .venv), invokes setup_env.sh to bootstrap deps + apply
# the verl patch. Subsequent runs skip straight to training.
set -e

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$BLOCK_DIR/repos/harbor-verl-train"
VENV="$REPO/.venv"

# Archive this run when start.sh exits (success, error, or signal).
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
_archive_run_on_exit() {
    local rc=$?
    bash "$BLOCK_DIR/scripts/archive_run.sh" "$rc" "$RUN_STARTED_AT" || true
    exit $rc
}
trap _archive_run_on_exit EXIT

if [[ ! -d "$VENV" ]]; then
    echo "[block/start] No .venv detected — bootstrapping via setup_env.sh ..."
    bash "$REPO/scripts/setup_env.sh"
fi

bash "$BLOCK_DIR/scripts/train_1node_cc.sh" "$@"
