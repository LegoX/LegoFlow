#!/usr/bin/env bash
# Clean this block's run artifacts.
#
#   (default)   Remove the temporary output of a run: proxy state, logs, caches.
#               Keeps the environment and everything expensive to regenerate —
#               most importantly the collected PR ids (days of collection under
#               GitHub API rate limits) and the verified SWE tasks.
#
#   --all       Wipe artifacts/ completely except git-tracked files. Asks for
#               confirmation twice.
#
# Git-tracked files are never removed in either mode.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BLOCK_NAME="$(basename "$BLOCK_DIR")"
ARTIFACTS_DIR="$BLOCK_DIR/artifacts"

# Kept in default mode. Rationale for the non-obvious ones:
#   collected_prs    — ~1.4 GB of PR ids; re-collecting costs days and burns
#                      GitHub API quota (run_001 lost 62885 PRs to rate limits)
#   swe_tasks        — tasks that passed NOP/Oracle verification; the manifest
#                      verifiable_tasks.txt is the downstream contract
#   merged_swe_tasks — extract_verified_tasks.py output, consumed by tracer
#   claude-config    — CLAUDE_CONFIG_DIR prepared by setup
#   state            — swegen's per-language --state-dir; it is what lets a run
#                      resume instead of re-processing every PR from scratch
KEEP_DEFAULT=(env envs index.yaml archives
              collected_prs swe_tasks merged_swe_tasks claude-config state)

MODE=default
DRY_RUN=0
ASSUME_YES=0

for arg in "$@"; do
    case "$arg" in
        -n|--dry-run) DRY_RUN=1 ;;
        --all)        MODE=all ;;
        --yes)        ASSUME_YES=1 ;;
        --outputs)
            echo "ERROR: --outputs was removed; its behaviour was ambiguous." >&2
            echo "  swe_tasks/ is now kept by default and only removed by --all," >&2
            echo "  which wipes artifacts/ entirely after confirmation." >&2
            exit 2
            ;;
        -h|--help)
            cat <<EOF
Usage: $(basename "$0") [--dry-run] [--all [--yes]]

  (no flags)  Remove temporary run output under $ARTIFACTS_DIR.
              Keeps: ${KEEP_DEFAULT[*]}
  --all       Wipe artifacts/ except git-tracked files (confirms twice).
  --dry-run   Print what would be removed and exit.
EOF
            exit 0
            ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

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

is_tracked() {
    [[ -n "$(git -C "$BLOCK_DIR" ls-files -- "$1" 2>/dev/null | head -1)" ]]
}

if [[ "$MODE" == "all" && "$DRY_RUN" == "0" && "$ASSUME_YES" == "0" ]]; then
    echo "############################################################"
    echo "  CLEAN ALL — $BLOCK_NAME: wipes $ARTIFACTS_DIR entirely,"
    echo "  keeping only git-tracked files. This deletes the venv, the"
    echo "  collected PR ids (~1.4 GB, days of GitHub API collection),"
    echo "  every generated and verified SWE task, and all run archives."
    echo ""
    echo "  Make sure no swegen create/collect job is running."
    echo "############################################################"
    if [[ ! -t 0 ]]; then
        echo "ERROR: --all needs an interactive terminal (or pass --yes)." >&2
        exit 2
    fi
    read -r -p "Type 'yes' to continue: " reply
    [[ "$reply" == "yes" ]] || { echo "Aborted."; exit 1; }
    read -r -p "Type 'clean all $BLOCK_NAME' to proceed: " reply2
    [[ "$reply2" == "clean all $BLOCK_NAME" ]] || { echo "Aborted."; exit 1; }
fi

shopt -s nullglob dotglob
for entry in "$ARTIFACTS_DIR"/*; do
    name="$(basename "$entry")"
    skip=0
    if [[ "$MODE" == "default" ]]; then
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
