#!/usr/bin/env bash
# Purge intermediate artifacts for this block.
# Keeps: env/, envs/, index.yaml, archives/, jobs/, tasks/ under artifacts/.
# jobs/ and tasks/ are primary outputs — pass --outputs to remove them too.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS_DIR="$BLOCK_DIR/artifacts"

DRY_RUN=0
REMOVE_OUTPUTS=0
for arg in "$@"; do
    case "$arg" in
        -n|--dry-run) DRY_RUN=1 ;;
        --outputs) REMOVE_OUTPUTS=1 ;;
        -h|--help)
            echo "Usage: $(basename "$0") [--dry-run] [--outputs]"
            echo "Removes intermediates under $ARTIFACTS_DIR (logs/, litellm/, etc.)."
            echo "Pass --outputs to also remove jobs/ and tasks/."
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
        jobs|tasks) [[ "$REMOVE_OUTPUTS" == "0" ]] && continue ;;
    esac
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "  [dry-run] would remove: $entry"
    else
        echo "  removing: $entry"
        rm -rf "$entry"
    fi
done

echo "  clean done for $(basename "$BLOCK_DIR")."
