#!/usr/bin/env bash
# Purge intermediate artifacts for this block.
# Keeps: env/, envs/, index.yaml, archives/, checkpoints/ under artifacts/.
# checkpoints/ is a primary output — pass --outputs to remove it too.
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
            echo "Removes intermediates under $ARTIFACTS_DIR (logs/, wandb/, etc.)."
            echo "Pass --outputs to also remove checkpoints/."
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
        checkpoints) [[ "$REMOVE_OUTPUTS" == "0" ]] && continue ;;
    esac
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "  [dry-run] would remove: $entry"
    else
        echo "  removing: $entry"
        rm -rf "$entry"
    fi
done

# Also clean Ray temp files from /tmp (original behavior).
if [[ "$DRY_RUN" == "1" ]]; then
    echo "  [dry-run] would remove: /tmp/ray /tmp/trajectory_output_dir /tmp/trajectory_output_dir.txt"
else
    rm -rf /tmp/ray /tmp/trajectory_output_dir /tmp/trajectory_output_dir.txt 2>/dev/null || true
fi

echo "  clean done for $(basename "$BLOCK_DIR")."
