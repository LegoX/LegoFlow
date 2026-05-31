#!/bin/bash
# Entry point — edit config.yaml first, then run this.
set -e

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Archive this run when start.sh exits (success, error, or signal).
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
_archive_run_on_exit() {
    local rc=$?
    bash "$BLOCK_DIR/scripts/archive_run.sh" "$rc" "$RUN_STARTED_AT" || true
    exit $rc
}
trap _archive_run_on_exit EXIT

bash "$BLOCK_DIR/scripts/train_1node.sh"
