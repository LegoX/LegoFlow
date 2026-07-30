#!/usr/bin/env bash
# Keep merged_tasks_dir in step with the per-language pools WHILE generation runs.
#
# create_all_bg.sh detaches eight language workers and returns immediately, so
# start.sh exits within seconds while tasks keep landing for hours. Aggregating
# once at the end would mean the downstream consumer (tracer reads
# curator.output.merged_tasks_dir) sees nothing until the whole run finishes —
# and nothing invoked the aggregation at all, so a fresh run left the directory
# absent and an existing one served a stale merge.
#
# This loops extract_verified_tasks.py on a timer instead. Each pass is a stat
# per verified task (~2s at 300 tasks), so a short interval is cheap. It exits
# once no create_<lang>.sh worker is left, after one final pass to pick up
# whatever landed in the last interval.
#
# Env:
#   AGGREGATE_INTERVAL   seconds between passes (default 300)
#   AGGREGATE_MAX_HOURS  hard stop, so a stuck worker cannot leave this running
#                        forever (default 24)
set -uo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BLOCK_DIR"

INTERVAL="${AGGREGATE_INTERVAL:-300}"
MAX_HOURS="${AGGREGATE_MAX_HOURS:-24}"
PY_BIN="${PY_BIN:-artifacts/envs/swegen-env/bin/python}"
[[ -x "$PY_BIN" ]] || PY_BIN=python3

LOG_DIR="$BLOCK_DIR/artifacts/logs"
mkdir -p "$LOG_DIR"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

# `pgrep -f` matches the whole command line, so it also hits any wrapper whose
# own arguments happen to mention a create script — including this loop's own
# shell when it is launched from such a command. Drop self and parent, or the
# loop can never observe the workers finishing and runs to the ceiling.
workers_alive() {
    local pid
    while read -r pid; do
        [[ -z "$pid" || "$pid" == "$$" || "$pid" == "$PPID" ]] && continue
        return 0
    done < <(pgrep -f "scripts/create_(py|js|ts|go|c|cpp|java|rust)\.sh" 2>/dev/null)
    return 1
}

aggregate() {
    "$PY_BIN" scripts/extract_verified_tasks.py --quiet 2>&1 | sed 's/^/  /'
}

DEADLINE=$(( $(date +%s) + MAX_HOURS * 3600 ))
log "aggregator started (interval=${INTERVAL}s, max=${MAX_HOURS}h)"

# One pass up front: a re-run over an existing pool should publish what is
# already verified without waiting a full interval for it.
aggregate

while true; do
    if ! workers_alive; then
        log "no create_<lang>.sh workers left — final pass"
        aggregate
        log "aggregator done"
        exit 0
    fi
    if (( $(date +%s) >= DEADLINE )); then
        log "WARN: hit the ${MAX_HOURS}h ceiling with workers still alive — final pass, then stopping"
        aggregate
        exit 0
    fi
    sleep "$INTERVAL"
    aggregate
done
