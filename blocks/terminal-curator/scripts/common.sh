#!/usr/bin/env bash
# Shared path/argument handling. Paths in config are relative to the block.
set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$BLOCK_DIR/../.." && pwd)"
CONFIG="${TERMINAL_CURATOR_CONFIG:-$BLOCK_DIR/config.yaml}"
PYTHON="${TERMINAL_CURATOR_PYTHON:-$BLOCK_DIR/artifacts/env/terminal-curator/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON=python3
parse_config() {
  if [[ "${1:-}" == --config ]]; then
    [[ $# -ge 2 ]] || { echo 'ERROR: --config requires a file' >&2; exit 2; }
    CONFIG="$2"
    [[ "$CONFIG" = /* ]] || CONFIG="$BLOCK_DIR/$CONFIG"
    CONFIG_ARGS_CONSUMED=2
  else
    CONFIG_ARGS_CONSUMED=0
  fi
}
