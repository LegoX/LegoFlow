#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
parse_config "$@"; shift "$CONFIG_ARGS_CONSUMED"
run_args=()
if [[ "${1:-}" == --run-id ]]; then
  [[ $# -ge 2 ]] || { echo 'ERROR: --run-id requires an ID' >&2; exit 2; }
  run_args=(--run-id "$2"); shift 2
fi
[[ $# -le 2 ]] || { echo 'Usage: archive_run.sh [--config FILE] [--run-id ID] [exit_code] [started_at]' >&2; exit 2; }
exec "$PYTHON" "$BLOCK_DIR/scripts/terminal_curator.py" --config "$CONFIG" archive --exit-code "${1:-0}" --started-at "${2:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}" "${run_args[@]}"
