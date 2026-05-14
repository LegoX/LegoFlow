#!/bin/bash
# Remove temporary outputs. Does NOT delete model checkpoints by default.
#   bash scripts/clean.sh              — only local script __pycache__
#   bash scripts/clean.sh --artifacts --yes
#   bash scripts/clean.sh --artifacts --dry-run
#   bash scripts/clean.sh --repo-cache --yes
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_DIR="$BLOCK_DIR/artifacts"

usage() {
    echo "Usage:"
    echo "  bash scripts/clean.sh"
    echo "  bash scripts/clean.sh --artifacts --dry-run"
    echo "  bash scripts/clean.sh --artifacts --yes"
    echo "  bash scripts/clean.sh --repo-cache --yes"
}

delete_path() {
    local target="$1"
    case "$target" in
        "$ARTIFACT_DIR"/*) ;;
        *)
            echo "ERROR: Refusing to delete outside artifacts/: $target" >&2
            exit 1
            ;;
    esac
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "    Would remove: $target"
    else
        rm -rf "$target"
    fi
}

DELETE_ARTIFACTS=false
DELETE_REPO_CACHE=false
DRY_RUN=false
CONFIRMED=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --artifacts) DELETE_ARTIFACTS=true ;;
        --repo-cache) DELETE_REPO_CACHE=true ;;
        --dry-run) DRY_RUN=true ;;
        --yes) CONFIRMED=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown option: $1" >&2; usage; exit 1 ;;
    esac
    shift
done

echo "=== Cleaning temporary files ==="

python3 - "$BLOCK_DIR/scripts" <<'PY'
import shutil
import sys
from pathlib import Path

for path in Path(sys.argv[1]).rglob("__pycache__"):
    shutil.rmtree(path, ignore_errors=True)
PY
echo "    Cleaned __pycache__ from scripts/"

if [[ "$DELETE_REPO_CACHE" == "true" ]]; then
    if [[ "$CONFIRMED" != "true" && "$DRY_RUN" != "true" ]]; then
        echo "ERROR: --repo-cache modifies repos/. Re-run with --repo-cache --dry-run or --repo-cache --yes."
        exit 1
    fi
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "    Would remove __pycache__ under repos/swe_data_process"
    else
        python3 - "$BLOCK_DIR/repos/swe_data_process" <<'PY'
import shutil
import sys
from pathlib import Path

for path in Path(sys.argv[1]).rglob("__pycache__"):
    shutil.rmtree(path, ignore_errors=True)
PY
        echo "    Cleaned __pycache__ from repos/swe_data_process"
    fi
fi

if [[ "$DELETE_ARTIFACTS" == "true" ]]; then
    if [[ "$DRY_RUN" != "true" && "$CONFIRMED" != "true" ]]; then
        echo "ERROR: --artifacts deletes generated data, models, logs, and train YAMLs."
        echo "Re-run with --artifacts --dry-run to preview or --artifacts --yes to delete."
        exit 1
    fi

    echo ""
    echo "=== Cleaning gitignored runtime artifacts ==="
    delete_path "$BLOCK_DIR/artifacts/data/im_data"
    delete_path "$BLOCK_DIR/artifacts/data/lf_data"
    delete_path "$BLOCK_DIR/artifacts/model"
    delete_path "$BLOCK_DIR/artifacts/wandb"
    delete_path "$BLOCK_DIR/artifacts/logs"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "    Would remove: $BLOCK_DIR/artifacts/training_config/*.yaml"
    else
        rm -f "$BLOCK_DIR/artifacts/training_config/"*.yaml
    fi
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "    Dry run only; nothing was removed."
    else
        echo "    Removed: im_data/ lf_data/ model/ wandb/ logs/ training_config/*.yaml"
    fi
    echo "    Kept:    excluded_repos.txt  deepspeed/  实验追踪表.xlsx"
fi

echo "=== Clean done ==="
