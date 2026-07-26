#!/usr/bin/env bash
# Root clean: purge run artifacts at the root and in every subblock.
#
# Two modes, and only two:
#
#   (default)   Remove the temporary output of a run. Keeps each block's
#               keep-list: environments, dependencies, run records, and
#               anything expensive to collect or regenerate (curator's
#               collected PRs, tracer's consumption ledger, trainer's
#               checkpoints, ...). Safe to run between runs.
#
#   --purge     Wipe each block's artifacts/ completely, EXCEPT files tracked
#               by git. This destroys environments and every collected or
#               generated dataset. Requires repeated explicit confirmation.
#
# Files tracked by git are never removed in either mode — artifacts/ holds a
# few tracked inputs (e.g. artifacts/.gitkeep) and clean.sh must not dirty the
# working tree.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS_DIR="$ROOT_DIR/artifacts"

MODE=default
DRY_RUN=0
ASSUME_YES=0

for arg in "$@"; do
    case "$arg" in
        -n|--dry-run) DRY_RUN=1 ;;
        --purge)      MODE=purge ;;
        --yes)        ASSUME_YES=1 ;;
        --outputs)
            echo "ERROR: --outputs was removed; its behaviour was ambiguous." >&2
            echo "  Primary outputs are now kept by default and only removed by --purge," >&2
            echo "  which wipes artifacts/ entirely and asks for confirmation first." >&2
            exit 2
            ;;
        -h|--help)
            cat <<EOF
Usage: $(basename "$0") [--dry-run] [--purge [--yes]]

  (no flags)  Remove temporary run output under <block>/artifacts/.
              Keeps environments, dependencies, run records, and everything
              expensive to collect or regenerate.

  --purge     Wipe every block's artifacts/ except git-tracked files. This
              deletes environments, collected PRs, trajectories, datasets and
              checkpoints. Asks for confirmation twice; --yes skips the prompts
              (for automation only).

  --dry-run   Print what would be removed and exit without deleting.

Applies to the root block and every subblock under subblock/.
EOF
            exit 0
            ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# Root's own keep-list. Subblocks each define their own inside their clean.sh.
KEEP_DEFAULT=(env envs index.yaml archives)

is_tracked() {  # any git-tracked file at or under this path?
    [[ -n "$(git -C "$ROOT_DIR" ls-files -- "$1" 2>/dev/null | head -1)" ]]
}

# Refuse to delete anything under a path that is not an explicit artifacts dir.
assert_safe_artifacts_dir() {
    local dir="$1" resolved
    resolved="$(python3 -c 'import sys,pathlib;print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$dir")"
    if [[ "$(basename "$resolved")" != "artifacts" || "$resolved" == "/" \
       || "$resolved" == "$ROOT_DIR" || ( -n "${HOME:-}" && "$resolved" == "$HOME" ) ]]; then
        echo "ERROR: refusing to clean unsafe artifacts path: $resolved" >&2
        exit 2
    fi
}

# Purge a directory. In default mode the caller's keep-list applies; in purge
# mode only git-tracked paths survive.
clean_artifacts_dir() {
    local dir="$1"; shift
    local keep=("$@")
    if [[ ! -d "$dir" ]]; then
        echo "  (no artifacts/ dir — skipping)"
        return 0
    fi
    assert_safe_artifacts_dir "$dir"
    shopt -s nullglob dotglob
    local entry name k skip
    for entry in "$dir"/*; do
        name="$(basename "$entry")"
        skip=0
        if [[ "$MODE" == "default" ]]; then
            for k in "${keep[@]}"; do
                [[ "$name" == "$k" ]] && { skip=1; break; }
            done
        fi
        if [[ "$skip" == "0" ]] && is_tracked "$entry"; then
            echo "  keeping (git-tracked): $name"
            skip=1
        fi
        [[ "$skip" == "1" ]] && continue
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "  [dry-run] would remove: $entry"
        else
            echo "  removing: $entry"
            rm -rf "$entry"
        fi
    done
    shopt -u nullglob dotglob
}

# --- purge confirmation ------------------------------------------------------
if [[ "$MODE" == "purge" && "$DRY_RUN" == "0" && "$ASSUME_YES" == "0" ]]; then
    echo "############################################################"
    echo "  PURGE: this wipes artifacts/ in the root block AND in"
    echo "  every subblock, keeping only git-tracked files."
    echo ""
    echo "  This deletes, among other things:"
    echo "    - uv/venv environments (artifacts/env, artifacts/envs)"
    echo "    - curator's collected PRs and generated SWE tasks"
    echo "    - tracer's trajectories, task pool and consumption ledger"
    echo "    - trainer's converted datasets and model checkpoints"
    echo "    - evaluator's job results and prepared gold datasets"
    echo "    - every run archive and artifacts/index.yaml"
    echo ""
    echo "  Re-running the pipeline from scratch after this takes days"
    echo "  and re-spends GitHub API quota and LLM tokens."
    echo "############################################################"
    if [[ ! -t 0 ]]; then
        echo "ERROR: --purge needs an interactive terminal (or pass --yes)." >&2
        exit 2
    fi
    read -r -p "Type 'yes' to continue: " reply
    [[ "$reply" == "yes" ]] || { echo "Aborted."; exit 1; }
    echo ""
    echo "Second confirmation — this cannot be undone."
    read -r -p "Type 'purge swe_lego_live' to proceed: " reply2
    [[ "$reply2" == "purge swe_lego_live" ]] || { echo "Aborted."; exit 1; }
    echo ""
fi

subblock_args() {
    local args=()
    [[ "$DRY_RUN" == "1" ]] && args+=(--dry-run)
    # The root already confirmed; subblocks must not prompt again.
    [[ "$MODE" == "purge" ]] && args+=(--purge --yes)
    printf '%s\n' "${args[@]}"
}

echo "=== Cleaning root: swe_lego_live (mode: $MODE) ==="
clean_artifacts_dir "$ARTIFACTS_DIR" "${KEEP_DEFAULT[@]}"

FAILED=()
for block_dir in "$ROOT_DIR/subblock"/*/; do
    block_name="$(basename "$block_dir")"
    clean_script="${block_dir}scripts/clean.sh"
    echo ""
    echo "=== Cleaning subblock: $block_name (mode: $MODE) ==="
    if [[ -f "$clean_script" ]]; then
        mapfile -t args < <(subblock_args)
        # A block's own clean.sh knows its keep-list; if it fails, report it.
        # Never fall back to the generic purge here — that used to silently
        # delete what the block's script deliberately preserves.
        if ! bash "$clean_script" ${args[@]+"${args[@]}"}; then
            echo "  ERROR: $block_name clean.sh failed — leaving it untouched." >&2
            FAILED+=("$block_name")
        fi
    else
        echo "  (no scripts/clean.sh — falling back to generic purge)"
        clean_artifacts_dir "${block_dir}artifacts" "${KEEP_DEFAULT[@]}"
    fi
done

echo ""
if (( ${#FAILED[@]} > 0 )); then
    echo "Clean finished with failures: ${FAILED[*]}" >&2
    exit 1
fi
echo "All clean done."
