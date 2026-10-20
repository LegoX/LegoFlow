#!/usr/bin/env bash
# Run one bounded batch; the invoking skill handles the user's run authorization.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
parse_config "$@"; shift "$CONFIG_ARGS_CONSUMED"
[[ $# -eq 0 ]] || { echo 'Usage: start.sh [--config FILE]' >&2; exit 2; }
mkdir -p "$BLOCK_DIR/artifacts"
command -v flock >/dev/null || { echo 'ERROR: flock is required to protect the run lifecycle' >&2; exit 1; }
# Keep cleanup out of the gap between the worker exiting and its EXIT archive.
exec 9>>"$BLOCK_DIR/artifacts/launch.lock"
flock -n 9 || { echo 'ERROR: another terminal-curator launch is active' >&2; exit 1; }
read -r started_at run_id < <("$PYTHON" -c 'from datetime import datetime, timezone; import uuid; n=datetime.now(timezone.utc); print(n.isoformat(), n.strftime("%Y%m%dT%H%M%SZ-")+uuid.uuid4().hex[:8])')
child_pid=''
finish() {
  rc=$?
  trap - EXIT INT TERM
  if [[ -n "$child_pid" ]]; then
    # The worker may already be reaped and have removed its process records;
    # its exact trial configs still identify sidecars that Harbor left behind.
    if bash "$BLOCK_DIR/scripts/stop.sh" --pid "$child_pid" --run-id "$run_id"; then
      :
    else
      cleanup_rc=$?
      echo 'ERROR: owned sidecar cleanup failed; inspect this run before restarting.' >&2
      [[ "$rc" -ne 0 ]] || rc="$cleanup_rc"
    fi
    wait "$child_pid" 2>/dev/null || true
  fi
  if "$PYTHON" "$BLOCK_DIR/scripts/terminal_curator.py" --config "$CONFIG" archive --exit-code "$rc" --started-at "$started_at" --run-id "$run_id"; then
    :
  else
    archive_rc=$?
    echo 'ERROR: run archive failed; inspect the local run evidence.' >&2
    [[ "$rc" -ne 0 ]] || rc="$archive_rc"
  fi
  if [[ -f "$BLOCK_DIR/artifacts/run.pid" ]] && [[ "$(cat "$BLOCK_DIR/artifacts/run.pid")" = "${child_pid:-none}" ]]; then
    rm -f "$BLOCK_DIR/artifacts/run.pid"
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
bash "$BLOCK_DIR/scripts/dryrun.sh" --config "$CONFIG"
"$PYTHON" "$BLOCK_DIR/scripts/terminal_curator.py" --config "$CONFIG" run --run-id "$run_id" &
child_pid=$!
wait "$child_pid"
