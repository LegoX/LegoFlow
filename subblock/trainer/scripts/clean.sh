#!/usr/bin/env bash
# Clean this block's run artifacts.
#
#   (default)   Remove the temporary output of a run: console logs, the
#               generated training YAML, offline WandB state. Keeps the uv env
#               and everything expensive to regenerate — converted datasets and
#               model checkpoints.
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
#   data  — downloaded Hub files + converted LF datasets + dataset_info.json
#   model — checkpoints (~131 GB for an 8B run); never disposable
# training_config/ is NOT kept: train.sh regenerates <run>.yaml every run. The
# DeepSpeed configs that used to live there now sit in scripts/deepspeed/.
KEEP_DEFAULT=(env envs index.yaml archives data model)

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
            echo "  data/ and model/ are now kept by default and only removed by" >&2
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
    echo "  keeping only git-tracked files. This deletes the uv env"
    echo "  (rebuild costs a long install_env.sh run), every converted"
    echo "  dataset, ALL MODEL CHECKPOINTS, and all run archives."
    echo ""
    echo "  Make sure no training job is running."
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
