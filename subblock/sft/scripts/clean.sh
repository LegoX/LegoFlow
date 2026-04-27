#!/bin/bash
# Remove temporary outputs. Does NOT delete model checkpoints by default.
#   bash scripts/clean.sh              — only __pycache__
#   bash scripts/clean.sh --artifacts  — also remove gitignored runtime data under artifacts/
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Cleaning temporary files ==="

# Remove Python __pycache__ from swe_data_process
find "$BLOCK_DIR/repos/swe_data_process" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
echo "    Cleaned __pycache__ from repos/swe_data_process"

if [[ "${1:-}" == "--artifacts" ]]; then
    echo ""
    echo "=== Cleaning gitignored runtime artifacts ==="
    rm -rf "$BLOCK_DIR/artifacts/data/im_data"
    rm -rf "$BLOCK_DIR/artifacts/data/lf_data"
    rm -rf "$BLOCK_DIR/artifacts/model"
    rm -rf "$BLOCK_DIR/artifacts/wandb"
    rm -rf "$BLOCK_DIR/artifacts/logs"
    rm -f  "$BLOCK_DIR/artifacts/training_config/"*.yaml
    echo "    Removed: im_data/ lf_data/ model/ wandb/ logs/ training_config/*.yaml"
    echo "    Kept:    excluded_repos.txt  deepspeed/  实验追踪表.xlsx"
fi

echo "=== Clean done ==="
