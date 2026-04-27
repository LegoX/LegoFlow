#!/bin/bash
# Entry point — edit inputs.yaml first, then run this.
set -e

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash "$BLOCK_DIR/scripts/train.sh"
