#!/bin/bash
# Data-only pipeline (no training):
#   STEP 0: Data conversion  (raw trajectories → IM JSONL → LF JSON)
#   STEP 1: (intentionally omitted) Dataset registration happens in train.sh
#
# Reads runtime config from config.yaml -> runtime_info.input.
# Run from anywhere: bash scripts/dataprep.sh
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve block root and repo paths
# ---------------------------------------------------------------------------
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWE_DP_REPO="$BLOCK_DIR/repos/swe_data_process"
SWE_DP_SRC="$SWE_DP_REPO/src"
CONFIG="$BLOCK_DIR/config.yaml"
CONFIG_PYTHON="${CONFIG_PYTHON:-python3}"

abspath() {
    local p="$1"
    if [[ -z "$p" ]]; then echo ""; elif [[ "$p" = /* ]]; then echo "$p"; else echo "$BLOCK_DIR/$p"; fi
}

meta_cfg() {
    "$CONFIG_PYTHON" "$BLOCK_DIR/scripts/config_value.py" "$CONFIG" meta_info "$1" --default "${2:-}"
}

SFT_UV_RAW="$(meta_cfg "environment.sft_uv")"
if [[ -z "$SFT_UV_RAW" ]]; then
    echo "ERROR: meta_info.environment.sft_uv is not set in $CONFIG"
    exit 1
fi
SFT_UV="$(abspath "$SFT_UV_RAW")"
SFT_PYTHON_VERSION="$(meta_cfg "environment.python_version" "3.12")"
LF_PYTHON="$SFT_UV/bin/python"

if [[ ! -x "$LF_PYTHON" ]]; then
    echo "ERROR: SFT uv Python not found at $LF_PYTHON"
    echo "  Create it with:"
    echo "  uv venv \"$SFT_UV\" --python \"${SFT_PYTHON_VERSION:-3.12}\""
    echo "  uv pip install --python \"$LF_PYTHON\" -e \"$BLOCK_DIR/repos/LLaMA-Factory\" -e \"$BLOCK_DIR/repos/swe_data_process\""
    exit 1
fi

cfg() {
    "$LF_PYTHON" "$BLOCK_DIR/scripts/config_value.py" "$CONFIG" runtime_input "$1" --default "${2:-}"
}

# ---------------------------------------------------------------------------
# Load config values (only source + conversion + dataset sections needed)
# ---------------------------------------------------------------------------
SOURCE_TYPE="$(cfg "source.type")"
[[ -z "$SOURCE_TYPE" ]] && SOURCE_TYPE="harbor_job"
SCAFFOLD="$(cfg "source.scaffold")"
JOB_DIR_RAW="$(cfg "source.job_dir")"
JOB_DIR="$(abspath "$JOB_DIR_RAW")"
HF_FILE_NAME="$(cfg "source.hf_file_name")"

MAX_INSTANCES="$(cfg "conversion.max_instances")"
EXCLUDE_REPOS_RAW="$(cfg "conversion.exclude_repos_file")"
EXCLUDE_REPOS_FILE="$(abspath "$EXCLUDE_REPOS_RAW")"
DATA_NAME="$(cfg "conversion.data_name")"
IM_OUTPUT="$BLOCK_DIR/artifacts/data/im_data/${DATA_NAME}.jsonl"
LF_OUTPUT="$BLOCK_DIR/artifacts/data/lf_data/${DATA_NAME}.json"

echo "=== sft-train data prep ==="
echo "    Block:       $BLOCK_DIR"
echo "    Source type: $SOURCE_TYPE"
echo "    Scaffold:    $SCAFFOLD"
echo "    LF output:   $LF_OUTPUT"

if [[ "$SOURCE_TYPE" != "harbor_job" ]]; then
    echo ""
    echo "=== Source type '$SOURCE_TYPE' uses a ready-made LF dataset — nothing to convert. ==="
    if [[ "$SOURCE_TYPE" == "hf_lf" && -n "$HF_FILE_NAME" ]]; then
        echo "    hf_lf:    scripts/train.sh downloads source.hf_file_name and registers that exact file."
    else
        echo "    hf_lf:    the dataset config is pulled from the HuggingFace Hub at train time."
    fi
    echo "    local_lf: source.lf_path is registered as-is."
    echo "    Run scripts/train.sh (or scripts/start.sh) to register the dataset and train."
    exit 0
fi

if [[ -z "$DATA_NAME" ]]; then
    echo "ERROR: conversion.data_name is empty — set runtime_info.input.conversion.data_name in config.yaml"
    exit 1
fi

# ---------------------------------------------------------------------------
# Determine converter module and build CLI args. The refactored swe_data_process
# package exposes job-dir based converters only.
# ---------------------------------------------------------------------------
CONVERT_ARGS=()

if [[ -z "$JOB_DIR" ]]; then
    echo "ERROR: source.job_dir is empty — set runtime_info.input.source.job_dir in config.yaml"
    exit 1
fi

case "${SCAFFOLD}" in
    openhands-sdk)
        CONVERTER_MODULE="swe_data_process.openhands.convert_openhands_sdk_to_im"
        ;;
    claude-code)
        CONVERTER_MODULE="swe_data_process.claudecode_opencode.convert_cc_to_im"
        ;;
    open-code)
        CONVERTER_MODULE="swe_data_process.claudecode_opencode.convert_oc_to_im"
        ;;
    terminus2)
        CONVERTER_MODULE="swe_data_process.terminus2.convert_terminus2_to_im"
        ;;
    *)
        echo "ERROR: Unsupported scaffold for job-dir conversion: '${SCAFFOLD}'"
        echo "  Valid scaffold: openhands-sdk | claude-code | open-code | terminus2"
        exit 1
        ;;
esac

CONVERT_ARGS+=(--job-dir "$JOB_DIR")
CONVERT_ARGS+=(--im-output "$IM_OUTPUT" --lf-output "$LF_OUTPUT")
if [[ -n "$MAX_INSTANCES" ]] && [[ "$MAX_INSTANCES" -gt 0 ]] 2>/dev/null; then
    CONVERT_ARGS+=(--max-instances "$MAX_INSTANCES")
fi
if [[ -n "$EXCLUDE_REPOS_RAW" ]]; then
    if [[ ! -f "$EXCLUDE_REPOS_FILE" ]]; then
        echo "ERROR: conversion.exclude_repos_file not found: $EXCLUDE_REPOS_FILE"
        exit 1
    fi
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
    LF_COUNT=$("$LF_PYTHON" -c 'import json, sys; print(len(json.load(open(sys.argv[1], encoding="utf-8"))))' "$LF_OUTPUT" 2>/dev/null || echo "?")
    echo "=== IM output already exists ($IM_LINES lines): $IM_OUTPUT ==="
    echo "=== LF output already exists ($LF_COUNT records): $LF_OUTPUT ==="
    echo "    Skipping conversion. Delete both files to re-run."
elif [[ -f "$IM_OUTPUT" || -f "$LF_OUTPUT" ]]; then
    echo "ERROR: Found a partial conversion output."
    echo "  IM: $IM_OUTPUT"
    echo "  LF: $LF_OUTPUT"
    echo "Delete the existing partial file or restore the missing pair before re-running."
    exit 1
else
    echo "=== Running converter module: $CONVERTER_MODULE ==="
    echo "    Args: ${CONVERT_ARGS[*]}"
    PYTHONPATH="$SWE_DP_SRC:${PYTHONPATH:-}" "$LF_PYTHON" -m "$CONVERTER_MODULE" "${CONVERT_ARGS[@]}"
    echo "=== Conversion done ==="
fi

echo ""
echo "=== Data prep done. Output: $LF_OUTPUT ==="
