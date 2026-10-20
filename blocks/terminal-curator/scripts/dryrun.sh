#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
parse_config "$@"; shift "$CONFIG_ARGS_CONSUMED"
"$PYTHON" "$ROOT_DIR/scripts/validate_config.py" --block "$BLOCK_DIR" --config "$CONFIG"
exec "$PYTHON" "$BLOCK_DIR/scripts/terminal_curator.py" --config "$CONFIG" check "$@"
