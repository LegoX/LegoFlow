#!/usr/bin/env bash
# Root clean: purge intermediate artifacts at the root and in every subblock.
# Each block keeps only:
#   - env/ or envs/   (environment caches)
#   - index.yaml      (run index)
#   - archives/       (archived runs per BLOCK_DEFINITION)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS_DIR="$ROOT_DIR/artifacts"

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        -n|--dry-run) DRY_RUN=1 ;;
        -h|--help)
            cat <<EOF
Usage: $(basename "$0") [--dry-run]

Removes everything under <block>/artifacts/ except:
  env/ or envs/, index.yaml, archives/

Applies to the root block and every subblock under subblock/.
EOF
            exit 0
            ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

clean_artifacts_dir() {
    local dir="$1"
    if [[ ! -d "$dir" ]]; then
        echo "  (no artifacts/ dir — skipping)"
        return 0
    fi
    shopt -s nullglob dotglob
    local entry name
    for entry in "$dir"/*; do
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
}

echo "=== Cleaning root: swe_lego_live ==="
clean_artifacts_dir "$ARTIFACTS_DIR"

for block_dir in "$ROOT_DIR/subblock"/*/; do
    block_name="$(basename "$block_dir")"
    clean_script="${block_dir}scripts/clean.sh"
    echo ""
    echo "=== Cleaning subblock: $block_name ==="
    if [[ -f "$clean_script" ]]; then
        if [[ "$DRY_RUN" == "1" ]]; then
            bash "$clean_script" --dry-run || {
                echo "  WARN: $block_name clean.sh failed under --dry-run; falling back to direct purge"
                clean_artifacts_dir "${block_dir}artifacts"
            }
        else
            bash "$clean_script" || {
                echo "  WARN: $block_name clean.sh exited non-zero; falling back to direct purge"
                clean_artifacts_dir "${block_dir}artifacts"
            }
        fi
    else
        echo "  (no scripts/clean.sh — falling back to direct purge)"
        clean_artifacts_dir "${block_dir}artifacts"
    fi
done

echo ""
echo "All clean done."
