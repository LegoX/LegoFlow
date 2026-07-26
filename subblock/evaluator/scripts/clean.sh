#!/usr/bin/env bash
# Clean this block's run artifacts.
#
#   (default)   Remove the temporary output of a run: disposable logs and
#               generated proxy state. Keeps the environments and everything
#               expensive or impossible to reconstruct from artifacts/archives
#               alone — job results, prepared gold datasets, extracted agent
#               runtimes, and run records.
#
#   --purge     Wipe artifacts/ completely except git-tracked files. Asks for
#               confirmation twice.
#
# Git-tracked files are never removed in either mode.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BLOCK_NAME="$(basename "$BLOCK_DIR")"
ARTIFACTS_DIR="${EVAL_ARTIFACTS_DIR:-$BLOCK_DIR/artifacts}"

# Kept in default mode. Rationale for the non-obvious ones:
#   jobs     — per-task eval results and trajectories; rerunning costs tokens
#   datasets — prepared Harbor gold datasets (adapter + LLM tagging)
#   runtime  — extracted agent runtimes
KEEP_DEFAULT=(env envs index.yaml archives jobs datasets runtime)
# Concurrency-control files, never data. Kept in BOTH modes so a purge cannot
# break the mutual exclusion that protects a running smoke.
KEEP_ALWAYS=(.archive.lock .smoke.lock)

MODE=default
DRY_RUN=0
ASSUME_YES=0

for arg in "$@"; do
    case "$arg" in
        -n|--dry-run) DRY_RUN=1 ;;
        --purge)      MODE=purge ;;
        --yes)        ASSUME_YES=1 ;;
        --outputs)
            echo "ERROR: --outputs is not supported; primary outputs are kept by" >&2
            echo "  default and only removed by --purge, which wipes artifacts/" >&2
            echo "  entirely after confirmation." >&2
            exit 2
            ;;
        -h|--help)
            cat <<EOF
Usage: $(basename "$0") [--dry-run] [--purge [--yes]]

  (no flags)  Remove temporary run output under $ARTIFACTS_DIR.
              Keeps: ${KEEP_DEFAULT[*]}
  --purge     Wipe artifacts/ except git-tracked files (confirms twice).
  --dry-run   Print what would be removed and exit.
EOF
            exit 0
            ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# Resolve symlinks and reject broad/custom roots before any recursive deletion.
# EVAL_ARTIFACTS_DIR exists for isolated tests and alternate artifact volumes,
# but the final path must still be an explicitly named artifacts directory.
ARTIFACTS_DIR="$(python3 -c 'import sys,pathlib;print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$ARTIFACTS_DIR")"
if [[ "$(basename "$ARTIFACTS_DIR")" != "artifacts" || "$ARTIFACTS_DIR" == "/" \
   || "$ARTIFACTS_DIR" == "$BLOCK_DIR" || ( -n "${HOME:-}" && "$ARTIFACTS_DIR" == "$HOME" ) ]]; then
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

is_tracked() {
    [[ -n "$(git -C "$BLOCK_DIR" ls-files -- "$1" 2>/dev/null | head -1)" ]]
}

if [[ "$MODE" == "purge" && "$DRY_RUN" == "0" && "$ASSUME_YES" == "0" ]]; then
    echo "############################################################"
    echo "  PURGE $BLOCK_NAME: wipes $ARTIFACTS_DIR entirely,"
    echo "  keeping only git-tracked files. This deletes the Harbor uv"
    echo "  env and LiteLLM venv, every eval job result and trajectory,"
    echo "  the prepared gold datasets, the extracted agent runtimes,"
    echo "  and all run archives."
    echo ""
    echo "  Make sure no eval job is running."
    echo "############################################################"
    if [[ ! -t 0 ]]; then
        echo "ERROR: --purge needs an interactive terminal (or pass --yes)." >&2
        exit 2
    fi
    read -r -p "Type 'yes' to continue: " reply
    [[ "$reply" == "yes" ]] || { echo "Aborted."; exit 1; }
    read -r -p "Type 'purge $BLOCK_NAME' to proceed: " reply2
    [[ "$reply2" == "purge $BLOCK_NAME" ]] || { echo "Aborted."; exit 1; }
fi

shopt -s nullglob dotglob
for entry in "$ARTIFACTS_DIR"/*; do
    name="$(basename "$entry")"
    skip=0
    for k in "${KEEP_ALWAYS[@]}"; do
        [[ "$name" == "$k" ]] && { skip=1; break; }
    done
    if [[ "$skip" == "0" && "$MODE" == "default" ]]; then
        for k in "${KEEP_DEFAULT[@]}"; do
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

echo "  clean done for $BLOCK_NAME (mode: $MODE)."
