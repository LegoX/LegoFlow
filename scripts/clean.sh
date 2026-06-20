#!/usr/bin/env bash
# Root clean: purge intermediate artifacts at the root and in every subblock.
# Each block keeps: env/, envs/, index.yaml, archives/, and its primary outputs.
# Pass --outputs to also remove primary outputs (swe_tasks/, jobs/, model/, etc.).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS_DIR="$ROOT_DIR/artifacts"

DRY_RUN=0
REMOVE_OUTPUTS=0
for arg in "$@"; do
    case "$arg" in
        -n|--dry-run) DRY_RUN=1 ;;
        --outputs) REMOVE_OUTPUTS=1 ;;
        -h|--help)
            cat <<EOF
Usage: $(basename "$0") [--dry-run] [--outputs]

Removes intermediates under <block>/artifacts/ (logs, caches, etc.).
Each block's primary outputs (swe_tasks/, jobs/, model/, checkpoints/) are
preserved unless --outputs is passed.

Applies to the root block and every subblock under subblock/.
EOF
            exit 0
            ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# Fallback purge used when a subblock has no clean.sh.
# Keeps env/envs/index.yaml/archives always; keeps primary outputs unless --outputs.
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

build_subblock_args() {
    local args=()
    [[ "$DRY_RUN" == "1" ]] && args+=(--dry-run)
    [[ "$REMOVE_OUTPUTS" == "1" ]] && args+=(--outputs)
    echo "${args[@]}"
}

echo "=== Cleaning root: lego_factory ==="
clean_artifacts_dir "$ARTIFACTS_DIR"

for block_dir in "$ROOT_DIR/subblock"/*/; do
    block_name="$(basename "$block_dir")"
    clean_script="${block_dir}scripts/clean.sh"
    echo ""
    echo "=== Cleaning subblock: $block_name ==="
    if [[ -f "$clean_script" ]]; then
        # shellcheck disable=SC2046
        bash "$clean_script" $(build_subblock_args) || {
            echo "  WARN: $block_name clean.sh exited non-zero; falling back to direct purge"
            clean_artifacts_dir "${block_dir}artifacts"
        }
    else
        echo "  (no scripts/clean.sh — falling back to direct purge)"
        clean_artifacts_dir "${block_dir}artifacts"
    fi
done

echo ""
echo "All clean done."
