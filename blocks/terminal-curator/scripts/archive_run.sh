#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
parse_config "$@"; shift "$CONFIG_ARGS_CONSUMED"
exec "$PYTHON" "$BLOCK_DIR/scripts/terminal_curator.py" --config "$CONFIG" archive --exit-code "${1:-0}" --started-at "${2:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
