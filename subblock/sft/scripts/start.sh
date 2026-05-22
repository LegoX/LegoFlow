#!/bin/bash
# Entry point: edit config.yaml runtime_info.input first, then run this.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash "$BLOCK_DIR/scripts/train.sh"
