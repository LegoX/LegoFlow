#!/usr/bin/env bash
# Clean this block's run artifacts.
#
#   (default)   Remove the temporary output of a run: proxy state, logs, launch
#               logs. Keeps the environment and everything expensive to
#               regenerate — Harbor jobs, the prepared task pool, converted SFT
#               data, the extracted agent runtime, and the consumption ledger
#               (the source of truth for which tasks were already processed).
#
#   --purge     Wipe artifacts/ completely except git-tracked files. Asks for
#               confirmation twice.
#
# Git-tracked files are never removed in either mode.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BLOCK_NAME="$(basename "$BLOCK_DIR")"
ARTIFACTS_DIR="$BLOCK_DIR/artifacts"

# Kept in default mode. Rationale for the non-obvious ones:
#   consumption_ledger.yaml — source of truth for processed task IDs; losing it
#                             makes tracer re-run every task it already paid for
#   agent-runtime           — extracted agent runtime (~360 MB), costly to rebuild
#   jobs / tasks / sft_data — the block's declared outputs, consumed downstream
KEEP_DEFAULT=(env envs index.yaml archives jobs tasks sft_data
              consumption_ledger.yaml agent-runtime)

# Generator output living outside artifacts/; disposable in both modes.
EXTRA_PATHS=("$BLOCK_DIR/dashboard/site" "$BLOCK_DIR/dashboard/.cache")

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
            echo "  jobs/ and tasks/ are now kept by default and only removed by" >&2
            echo "  --purge, which wipes artifacts/ entirely after confirmation." >&2
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

# Resolve and refuse broad roots before any recursive deletion.
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

if [[ "$MODE" == "purge" && "$DRY_RUN" == "0" && "$ASSUME_YES" == "0" ]]; then
    echo "############################################################"
    echo "  PURGE $BLOCK_NAME: wipes $ARTIFACTS_DIR entirely,"
    echo "  keeping only git-tracked files. This deletes the uv env,"
    echo "  all Harbor jobs and trajectories, the prepared task pool,"
    echo "  converted SFT data, the extracted agent runtime, every run"
    echo "  archive, and the consumption ledger."
    echo ""
    echo "  Make sure no tracer job is running before you continue."
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

remove_entry() {
    local entry="$1"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "  [dry-run] would remove: $entry"
    else
        echo "  removing: $entry"
        rm -rf "$entry"
    fi
}

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
    remove_entry "$entry"
done
shopt -u nullglob dotglob

for extra in "${EXTRA_PATHS[@]}"; do
    [[ -e "$extra" ]] || continue
    if is_tracked "$extra"; then
        echo "  keeping (git-tracked): $extra"
        continue
    fi
    remove_entry "$extra"
done

echo "  clean done for $BLOCK_NAME (mode: $MODE)."
