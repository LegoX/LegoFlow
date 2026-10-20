#!/usr/bin/env bash
# Run one bounded batch; the invoking skill handles the user's run authorization.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
parse_config "$@"; shift "$CONFIG_ARGS_CONSUMED"
[[ $# -eq 0 ]] || { echo 'Usage: start.sh [--config FILE]' >&2; exit 2; }
mkdir -p "$BLOCK_DIR/artifacts"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
child_pid=''
finish() {
  rc=$?
  trap - EXIT INT TERM
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  "$PYTHON" "$BLOCK_DIR/scripts/terminal_curator.py" --config "$CONFIG" archive --exit-code "$rc" --started-at "$started_at" || true
  if [[ -f "$BLOCK_DIR/artifacts/run.pid" ]] && [[ "$(cat "$BLOCK_DIR/artifacts/run.pid")" = "${child_pid:-none}" ]]; then
    rm -f "$BLOCK_DIR/artifacts/run.pid"
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
bash "$BLOCK_DIR/scripts/dryrun.sh" --config "$CONFIG"
"$PYTHON" "$BLOCK_DIR/scripts/terminal_curator.py" --config "$CONFIG" run &
child_pid=$!
wait "$child_pid"
