#!/bin/bash
# Data-only pipeline (no training):
#   STEP 0: Data conversion  (raw trajectories → IM JSONL → LF JSON)
#   STEP 1: Dataset registration  (LF JSON → dataset_info.json)
#
# Reads all config from inputs.yaml — edit that file before running.
# Run from anywhere: bash scripts/dataprep.sh
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve block root and repo paths
# ---------------------------------------------------------------------------
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWE_DP_SRC="$BLOCK_DIR/repos/swe_data_process/src/swe_data_process"
CONFIG="$BLOCK_DIR/inputs.yaml"
LF_PYTHON="/anaconda3/envs/swelf/bin/python3"

if [[ ! -x "$LF_PYTHON" ]]; then
    echo "ERROR: swelf Python not found at $LF_PYTHON"
    echo "  Create the environment first: conda create -n swelf python=3.12"
    exit 1
fi

cfg() {
    "$LF_PYTHON" - "$CONFIG" "$1" <<'PYEOF'
import sys
import yaml

config_path, expr = sys.argv[1], sys.argv[2]

with open(config_path, encoding="utf-8") as fh:
    config = yaml.safe_load(fh)

value = eval(f"config{expr}", {"__builtins__": {}}, {"config": config})
print("" if value is None else value)
PYEOF
}

abspath() {
    local p="$1"
    if [[ -z "$p" ]]; then echo ""; elif [[ "$p" = /* ]]; then echo "$p"; else echo "$BLOCK_DIR/$p"; fi
}

# ---------------------------------------------------------------------------
# Load config values (only source + conversion + dataset sections needed)
# ---------------------------------------------------------------------------
PROVIDER="$(cfg "['source']['provider']")"
SCAFFOLD="$(cfg "['source']['scaffold']")"
JOB_DIR="$(cfg "['source']['job_dir']")"
TRAJS_DIR="$(cfg "['source']['trajs_dir']")"
SOURCE_DIR="$(cfg "['source']['source_dir']")"

MAX_INSTANCES="$(cfg "['conversion']['max_instances']")"
EXCLUDE_REPOS_RAW="$(cfg "['conversion']['exclude_repos_file']")"
EXCLUDE_REPOS_FILE="$(abspath "$EXCLUDE_REPOS_RAW")"
DATA_NAME="$(cfg "['conversion']['data_name']")"
IM_OUTPUT="$BLOCK_DIR/artifacts/data/im_data/${DATA_NAME}.jsonl"
LF_OUTPUT="$BLOCK_DIR/artifacts/data/lf_data/${DATA_NAME}.json"

echo "=== sft-train data prep ==="
echo "    Block:     $BLOCK_DIR"
echo "    Provider:  $PROVIDER  Scaffold: $SCAFFOLD"
echo "    LF output: $LF_OUTPUT"

if [[ -z "$DATA_NAME" ]]; then
    echo "ERROR: conversion.data_name is empty — set it in inputs.yaml"
    exit 1
fi

# ---------------------------------------------------------------------------
# Determine converter script and build CLI args
# ---------------------------------------------------------------------------
CONVERT_ARGS=()

case "${PROVIDER}+${SCAFFOLD}" in
    jierun+openhands-sdk)
        CONVERTER="openhands/convert_openhands_sdk_jierun_to_im.py"
        CONVERT_ARGS+=(--job-dir "$JOB_DIR")
        [[ -n "$TRAJS_DIR" ]] && CONVERT_ARGS+=(--trajs-dir "$TRAJS_DIR")
        ;;
    jierun+claude-code)
        CONVERTER="claudecode_opencode/convert_cc_jierun_to_im.py"
        CONVERT_ARGS+=(--job-dir "$JOB_DIR")
        [[ -n "$TRAJS_DIR" ]] && CONVERT_ARGS+=(--trajs-dir "$TRAJS_DIR")
        ;;
    jierun+open-code)
        CONVERTER="claudecode_opencode/convert_oc_jierun_to_im.py"
        CONVERT_ARGS+=(--job-dir "$JOB_DIR")
        [[ -n "$TRAJS_DIR" ]] && CONVERT_ARGS+=(--trajs-dir "$TRAJS_DIR")
        ;;
    jierun+terminus2)
        CONVERTER="terminus2/convert_terminus2_jierun_to_im.py"
        CONVERT_ARGS+=(--job-dir "$JOB_DIR")
        ;;
    chaofan+openhands)
        CONVERTER="openhands/convert_openhands_chaofan_to_im.py"
        CONVERT_ARGS+=(--source-dir "$SOURCE_DIR")
        ;;
    chaofan+claude-code)
        CONVERTER="claudecode_opencode/convert_cc_chaofan_to_im.py"
        CONVERT_ARGS+=(--source-dir "$SOURCE_DIR")
        ;;
    chaofan+open-code)
        CONVERTER="claudecode_opencode/convert_oc_chaofan_to_im.py"
        CONVERT_ARGS+=(--source-dir "$SOURCE_DIR")
        ;;
    chaofan+terminus2)
        CONVERTER="terminus2/convert_terminus2_chaofan_to_im.py"
        CONVERT_ARGS+=(--source-dir "$SOURCE_DIR")
        ;;
    chaofan+openhands-sdk)
        CONVERTER="openhands/convert_openhands_sdk_chaofan_to_im.py"
        CONVERT_ARGS+=(--source-dir "$SOURCE_DIR")
        ;;
    *)
        echo "ERROR: Unknown provider+scaffold: '${PROVIDER}+${SCAFFOLD}'"
        echo "  Valid provider: jierun | chaofan"
        echo "  Valid scaffold: openhands-sdk | claude-code | open-code | terminus2 | openhands (chaofan only)"
        exit 1
        ;;
esac

CONVERT_ARGS+=(--im-output "$IM_OUTPUT" --lf-output "$LF_OUTPUT")
if [[ -n "$MAX_INSTANCES" ]] && [[ "$MAX_INSTANCES" -gt 0 ]] 2>/dev/null; then
    CONVERT_ARGS+=(--max-instances "$MAX_INSTANCES")
fi
if [[ -n "$EXCLUDE_REPOS_FILE" ]]; then
    CONVERT_ARGS+=(--exclude-repos-file "$EXCLUDE_REPOS_FILE")
fi

# ---------------------------------------------------------------------------
# STEP 0: Data conversion
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "STEP 0: Data conversion"
echo "============================================================"

LF_DIR="$(dirname "$LF_OUTPUT")"
IM_DIR="$(dirname "$IM_OUTPUT")"
mkdir -p "$LF_DIR" "$IM_DIR"

if [[ -f "$IM_OUTPUT" && -f "$LF_OUTPUT" ]]; then
    IM_LINES=$(wc -l < "$IM_OUTPUT" 2>/dev/null || echo "?")
    LF_COUNT=$("$LF_PYTHON" -c "import json; print(len(json.load(open('$LF_OUTPUT'))))" 2>/dev/null || echo "?")
    echo "=== IM output already exists ($IM_LINES lines): $IM_OUTPUT ==="
    echo "=== LF output already exists ($LF_COUNT records): $LF_OUTPUT ==="
    echo "    Skipping conversion. Delete both files to re-run."
else
    echo "=== Running converter: $CONVERTER ==="
    echo "    Args: ${CONVERT_ARGS[*]}"
    "$LF_PYTHON" "$SWE_DP_SRC/$CONVERTER" "${CONVERT_ARGS[@]}"
    echo "=== Conversion done ==="
fi

echo ""
echo "=== Data prep done. Output: $LF_OUTPUT ==="
