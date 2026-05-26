#!/usr/bin/env bash
# Purge intermediate artifacts for this block.
# Keeps only: env/ or envs/, index.yaml, archives/  under artifacts/.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS_DIR="$BLOCK_DIR/artifacts"

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        -n|--dry-run) DRY_RUN=1 ;;
        -h|--help)
            echo "Usage: $(basename "$0") [--dry-run]"
            echo "Removes everything under $ARTIFACTS_DIR except env/, envs/, index.yaml, archives/."
            exit 0
            ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

if [[ ! -d "$ARTIFACTS_DIR" ]]; then
    echo "  (no artifacts/ dir at $ARTIFACTS_DIR — nothing to clean)"
    exit 0
fi

shopt -s nullglob dotglob
for entry in "$ARTIFACTS_DIR"/*; do
    name="$(basename "$entry")"
    case "$name" in
        env|envs|index.yaml|archives) continue ;;
    esac
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "  [dry-run] would remove: $entry"
    else
        echo "  removing: $entry"
        rm -rf "$entry"
    fi
done

echo "  clean done for $(basename "$BLOCK_DIR")."
