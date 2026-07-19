#!/usr/bin/env bash
# Purge disposable logs and generated proxy state for this block.
# Preserves environments, run records, job results, prepared gold datasets, and
# extracted agent runtimes. Those are expensive or impossible to reconstruct
# from artifacts/archives alone.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS_DIR="${EVAL_ARTIFACTS_DIR:-$BLOCK_DIR/artifacts}"

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        -n|--dry-run) DRY_RUN=1 ;;
        -h|--help)
            echo "Usage: $(basename "$0") [--dry-run]"
            echo "Removes disposable entries under $ARTIFACTS_DIR."
            echo "Preserves env/, envs/, index.yaml, archives/, jobs/, datasets/, and runtime/."
            exit 0
            ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# Resolve symlinks and reject broad/custom roots before any recursive deletion.
# EVAL_ARTIFACTS_DIR exists for isolated tests and alternate artifact volumes,
# but the final path must still be an explicitly named artifacts directory.
ARTIFACTS_DIR="$(python3 - "$ARTIFACTS_DIR" <<'PY'
import sys
from pathlib import Path
print(Path(sys.argv[1]).expanduser().resolve())
PY
)"
if [[ "$(basename "$ARTIFACTS_DIR")" != "artifacts" \
    || "$ARTIFACTS_DIR" == "/" \
    || "$ARTIFACTS_DIR" == "$BLOCK_DIR" \
    || ( -n "${HOME:-}" && "$ARTIFACTS_DIR" == "${HOME:-}" ) ]]; then
    echo "ERROR: refusing to clean unsafe artifacts path: $ARTIFACTS_DIR" >&2
    exit 2
fi

if [[ ! -d "$ARTIFACTS_DIR" ]]; then
    echo "  (no artifacts/ dir at $ARTIFACTS_DIR — nothing to clean)"
    exit 0
fi

if command -v flock >/dev/null 2>&1; then
    exec 8>"$ARTIFACTS_DIR/.smoke.lock"
    if ! flock -n 8; then
        echo "ERROR: refusing to clean while an eval smoke is running" >&2
        exit 2
    fi
fi

shopt -s nullglob dotglob
for entry in "$ARTIFACTS_DIR"/*; do
    name="$(basename "$entry")"
    case "$name" in
        env|envs|index.yaml|archives|jobs|datasets|runtime|.archive.lock|.smoke.lock) continue ;;
    esac
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "  [dry-run] would remove: $entry"
    else
        echo "  removing: $entry"
        rm -rf "$entry"
    fi
done

echo "  clean done for $(basename "$BLOCK_DIR")."
