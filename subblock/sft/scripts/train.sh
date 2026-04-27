#!/bin/bash
# End-to-end SFT pipeline:
#   STEP 0: Data conversion  (raw trajectories → IM JSONL → LF JSON)
#   STEP 1: Dataset registration  (LF JSON → dataset_info.json)
#   STEP 2: Generate train config and launch SFT training
#
# Reads all config from inputs.yaml — edit that file before running.
# Run from anywhere: bash scripts/train.sh
# Assumes you are already on a compute node with GPUs available.
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

module load cuda12.4/toolkit/12.4.1

# Parse inputs.yaml with the swelf environment Python; returns "" for null/None values.
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
# Load config values
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

DATASET_NAME_RAW="$(cfg "['dataset']['name']")"

MODEL_PATH="$(cfg "['model']['model_name_or_path']")"
TRUST_REMOTE_CODE="$(cfg "['model']['trust_remote_code']")"

STAGE="$(cfg "['training']['stage']")"
FINETUNING_TYPE="$(cfg "['training']['finetuning_type']")"
DEEPSPEED="$(abspath "$(cfg "['training']['deepspeed']")")"
TEMPLATE="$(cfg "['training']['template']")"
CUTOFF_LEN="$(cfg "['training']['cutoff_len']")"
ROPE_SCALING="$(cfg "['training']['rope_scaling']")"
MAX_SAMPLES="$(cfg "['training']['max_samples']")"
PREPROCESSING_WORKERS="$(cfg "['training']['preprocessing_num_workers']")"
DATALOADER_WORKERS="$(cfg "['training']['dataloader_num_workers']")"
OUTPUT_DIR="$(cfg "['training']['output_dir']")"
SAVE_STRATEGY="$(cfg "['training']['save_strategy']")"
LOGGING_STEPS="$(cfg "['training']['logging_steps']")"
SAVE_STEPS="$(cfg "['training']['save_steps']")"
OVERWRITE_OUTPUT_DIR="$(cfg "['training']['overwrite_output_dir']")"
SAVE_ONLY_MODEL="$(cfg "['training']['save_only_model']")"
RESUME_FROM_CHECKPOINT="$(cfg "['training']['resume_from_checkpoint']")"
PER_DEVICE_BATCH="$(cfg "['training']['per_device_train_batch_size']")"
GRAD_ACCUM="$(cfg "['training']['gradient_accumulation_steps']")"
LR="$(cfg "['training']['learning_rate']")"
WEIGHT_DECAY="$(cfg "['training']['weight_decay']")"
MAX_GRAD_NORM="$(cfg "['training']['max_grad_norm']")"
EPOCHS="$(cfg "['training']['num_train_epochs']")"
LR_SCHEDULER="$(cfg "['training']['lr_scheduler_type']")"
WARMUP_RATIO="$(cfg "['training']['warmup_ratio']")"
BF16="$(cfg "['training']['bf16']")"
DDP_TIMEOUT="$(cfg "['training']['ddp_timeout']")"
ENABLE_LIGER="$(cfg "['training']['enable_liger_kernel']")"
USE_UNSLOTH_GC="$(cfg "['training']['use_unsloth_gc']")"
FLASH_ATTN="$(cfg "['training']['flash_attn']")"
RUN_NAME_RAW="$(cfg "['experiment']['run_name']")"
WANDB_API_KEY_VAL="$(cfg "['credentials']['wandb_api_key']")"
WANDB_MODE="$(cfg "['experiment']['wandb_mode']")"
WANDB_RUN_ID="$(cfg "['experiment']['wandb_run_id']")"

N_GPUS="$(cfg "['infrastructure']['n_gpus_per_node']")"

# Auto-derive dataset name from data_name if not explicitly set
if [[ -n "$DATASET_NAME_RAW" ]]; then
    DATASET_NAME="$DATASET_NAME_RAW"
else
    DATASET_NAME="$DATA_NAME"
fi

# Auto-derive run_name from output_dir basename if not explicitly set
if [[ -n "$RUN_NAME_RAW" ]]; then
    RUN_NAME="$RUN_NAME_RAW"
else
    RUN_NAME="$(basename "$OUTPUT_DIR")"
fi

echo "=== sft-train pipeline ==="
echo "    Block:     $BLOCK_DIR"
echo "    Provider:  $PROVIDER  Scaffold: $SCAFFOLD"
echo "    LF output: $LF_OUTPUT"
echo "    Dataset:   $DATASET_NAME"
echo "    Output dir: $OUTPUT_DIR"

if [[ -z "$DATA_NAME" ]]; then
    echo "ERROR: conversion.data_name is empty — set it in inputs.yaml"
    exit 1
fi

if [[ -z "$N_GPUS" || "$N_GPUS" -lt 1 ]] 2>/dev/null; then
    echo "ERROR: infrastructure.n_gpus_per_node must be a positive integer"
    exit 1
fi

if [[ -z "$RUN_NAME" ]]; then
    RUN_NAME="$DATASET_NAME"
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

# Common conversion args
CONVERT_ARGS+=(--im-output "$IM_OUTPUT" --lf-output "$LF_OUTPUT")
if [[ -n "$MAX_INSTANCES" ]] && [[ "$MAX_INSTANCES" -gt 0 ]] 2>/dev/null; then
    CONVERT_ARGS+=(--max-instances "$MAX_INSTANCES")
fi
if [[ -n "$EXCLUDE_REPOS_FILE" ]]; then
    CONVERT_ARGS+=(--exclude-repos-file "$EXCLUDE_REPOS_FILE")
fi

# ---------------------------------------------------------------------------
# STEP 0: Data conversion (idempotent — skipped if LF output already exists)
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

# ---------------------------------------------------------------------------
# STEP 1: Dataset registration in dataset_info.json (idempotent)
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "STEP 1: Dataset registration"
echo "============================================================"

DATASET_INFO="$BLOCK_DIR/artifacts/data/lf_data/dataset_info.json"
LF_FILENAME="$(basename "$LF_OUTPUT")"

mkdir -p "$BLOCK_DIR/artifacts/data/lf_data"
if [[ ! -f "$DATASET_INFO" ]]; then
    echo '{}' > "$DATASET_INFO"
fi

"$LF_PYTHON" - "$DATASET_INFO" "$DATASET_NAME" "$LF_FILENAME" <<'PYEOF'
import json
import sys

dataset_info_path, dataset_name, lf_filename = sys.argv[1:4]

with open(dataset_info_path) as f:
    info = json.load(f)

desired_entry = {
    "file_name": lf_filename,
    "formatting": "sharegpt",
    "columns": {"messages": "messages"},
    "tags": {
        "role_tag": "role",
        "content_tag": "content",
        "user_tag": "user",
        "assistant_tag": "assistant",
        "system_tag": "system"
    }
}

if dataset_name in info and info[dataset_name] == desired_entry:
    print(f"=== Dataset '{dataset_name}' already registered with the current LF file — skipping ===")
else:
    old_entry = info.get(dataset_name)
    info[dataset_name] = desired_entry
    with open(dataset_info_path, "w") as f:
        json.dump(info, f, indent=4, ensure_ascii=False)
    if old_entry is None:
        print(f"=== Registered dataset '{dataset_name}' → {lf_filename} ===")
    else:
        print(f"=== Updated dataset '{dataset_name}' mapping: {old_entry.get('file_name')} -> {lf_filename} ===")
PYEOF

# ---------------------------------------------------------------------------
# STEP 2: Update experiment tracking table
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "STEP 2: Update experiment tracking table"
echo "============================================================"
"$LF_PYTHON" "$BLOCK_DIR/scripts/update_tracking.py" --block-dir "$BLOCK_DIR"

# ---------------------------------------------------------------------------
# STEP 3: Generate LLaMA-Factory train YAML and launch training
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "STEP 3: Generate train config and launch training"
echo "============================================================"

if [[ -z "$OUTPUT_DIR" ]]; then
    echo "ERROR: training.output_dir is empty — set it in inputs.yaml"
    exit 1
fi

# Resolve OUTPUT_DIR to absolute path under artifacts/model/
if [[ "$OUTPUT_DIR" = /* ]]; then
    ABS_OUTPUT_DIR="$OUTPUT_DIR"
else
    ABS_OUTPUT_DIR="$BLOCK_DIR/artifacts/model/$(basename "$OUTPUT_DIR")"
fi

# Generate the LLaMA-Factory train YAML from inputs.yaml parameters
TRAIN_YAML_NAME="$(basename "$OUTPUT_DIR").yaml"
TRAIN_YAML_PATH="$BLOCK_DIR/artifacts/training_config/$TRAIN_YAML_NAME"

echo "=== Generating train config: artifacts/training_config/$TRAIN_YAML_NAME ==="
mkdir -p "$BLOCK_DIR/artifacts/training_config"

RESUME_LINE=""
if [[ -n "$RESUME_FROM_CHECKPOINT" && "$RESUME_FROM_CHECKPOINT" != "None" && "$RESUME_FROM_CHECKPOINT" != "null" ]]; then
    RESUME_LINE="resume_from_checkpoint: $RESUME_FROM_CHECKPOINT"
else
    RESUME_LINE="resume_from_checkpoint: null"
fi

if [[ "$WANDB_MODE" == "disabled" ]]; then
    REPORT_TO="none"
else
    REPORT_TO="wandb"
fi

cat > "$TRAIN_YAML_PATH" << EOF
### model
model_name_or_path: ${MODEL_PATH}
trust_remote_code: ${TRUST_REMOTE_CODE}

### method
stage: ${STAGE}
do_train: true
finetuning_type: ${FINETUNING_TYPE}
deepspeed: ${DEEPSPEED}

### dataset
dataset_dir: ${BLOCK_DIR}/artifacts/data/lf_data
dataset: ${DATASET_NAME}
template: ${TEMPLATE}
cutoff_len: ${CUTOFF_LEN}
rope_scaling: ${ROPE_SCALING}
max_samples: ${MAX_SAMPLES}
overwrite_cache: true
preprocessing_num_workers: ${PREPROCESSING_WORKERS}
dataloader_num_workers: ${DATALOADER_WORKERS}

### output
output_dir: ${ABS_OUTPUT_DIR}
run_name: ${RUN_NAME}
logging_steps: ${LOGGING_STEPS}
save_steps: ${SAVE_STEPS}
save_strategy: ${SAVE_STRATEGY}
plot_loss: true
overwrite_output_dir: ${OVERWRITE_OUTPUT_DIR}
save_only_model: ${SAVE_ONLY_MODEL}
report_to: ${REPORT_TO}
${RESUME_LINE}

### train
per_device_train_batch_size: ${PER_DEVICE_BATCH}
gradient_accumulation_steps: ${GRAD_ACCUM}
learning_rate: ${LR}
weight_decay: ${WEIGHT_DECAY}
max_grad_norm: ${MAX_GRAD_NORM}
num_train_epochs: ${EPOCHS}
lr_scheduler_type: ${LR_SCHEDULER}
warmup_ratio: ${WARMUP_RATIO}
bf16: ${BF16}
ddp_timeout: ${DDP_TIMEOUT}
enable_liger_kernel: ${ENABLE_LIGER}
use_unsloth_gc: ${USE_UNSLOTH_GC}
flash_attn: ${FLASH_ATTN}
EOF

echo "    Written: $TRAIN_YAML_PATH"

mkdir -p "$BLOCK_DIR/artifacts/logs"
TRAIN_LOG="$BLOCK_DIR/artifacts/logs/${RUN_NAME}_$(date +%Y%m%d_%H%M%S).log"

WANDB_MODE_CFG="$WANDB_MODE"
mkdir -p "$BLOCK_DIR/artifacts"
export WANDB_DIR="$BLOCK_DIR/artifacts"
case "$WANDB_MODE_CFG" in
    online)
        if [[ -z "$WANDB_API_KEY_VAL" ]]; then
            echo "ERROR: credentials.wandb_api_key is required when experiment.wandb_mode=online"
            exit 1
        fi
        export WANDB_API_KEY="$WANDB_API_KEY_VAL"
        export WANDB_MODE=online
        ;;
    offline)
        export WANDB_MODE=offline
        if [[ -n "$WANDB_API_KEY_VAL" ]]; then
            export WANDB_API_KEY="$WANDB_API_KEY_VAL"
        fi
        ;;
    disabled)
        unset WANDB_API_KEY
        unset WANDB_MODE
        unset WANDB_RUN_ID
        unset WANDB_RESUME
        ;;
    *)
        echo "ERROR: experiment.wandb_mode must be one of: online, offline, disabled"
        exit 1
        ;;
esac

if [[ "$WANDB_MODE_CFG" != "disabled" && -n "$WANDB_RUN_ID" ]]; then
    export WANDB_RESUME=allow
    export WANDB_RUN_ID="$WANDB_RUN_ID"
fi

echo "=== Launching training ==="
echo "    YAML:    $TRAIN_YAML_PATH"
echo "    Log:     $TRAIN_LOG"
echo "    GPUs:    $N_GPUS"
nvidia-smi

# Start status updater in background (refresh every 30s)
"$LF_PYTHON" "$BLOCK_DIR/scripts/update_status.py" --block-dir "$BLOCK_DIR" --loop 30 &
STATUS_PID=$!

FORCE_TORCHRUN=1 NPROC_PER_NODE="$N_GPUS" PYTHONPATH="$BLOCK_DIR/repos/LLaMA-Factory/src:${PYTHONPATH:-}" PATH="/anaconda3/envs/swelf/bin:$PATH" "$LF_PYTHON" -m llamafactory.cli train "$TRAIN_YAML_PATH" 2>&1 | tee "$TRAIN_LOG"

# Stop status updater and do a final refresh
kill "$STATUS_PID" 2>/dev/null || true
wait "$STATUS_PID" 2>/dev/null || true
"$LF_PYTHON" "$BLOCK_DIR/scripts/update_status.py" --block-dir "$BLOCK_DIR"

# ---------------------------------------------------------------------------
# STEP 4: Update outputs.yaml
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "STEP 4: Update outputs.yaml"
echo "============================================================"
"$LF_PYTHON" - "$BLOCK_DIR" "$ABS_OUTPUT_DIR" "$TRAIN_LOG" <<'PYEOF'
import json
import os
import re
import sys
from pathlib import Path

import yaml

block_dir, output_dir, train_log = sys.argv[1:4]
outputs_path = Path(block_dir) / "outputs.yaml"

with open(outputs_path, encoding="utf-8") as f:
    outputs = yaml.safe_load(f)

# checkpoint_path: find the latest checkpoint-* subdir, or use output_dir itself
out = Path(output_dir)
ckpts = sorted(out.glob("checkpoint-*"), key=lambda p: p.stat().st_mtime, reverse=True) if out.exists() else []
outputs["checkpoint_path"]["value"] = str(ckpts[0]) if ckpts else str(out)

# training_log
outputs["training_log"]["value"] = train_log

# wandb_run_id: read from WANDB_RUN_ID env or scan wandb dir
run_id = os.environ.get("WANDB_RUN_ID", "")
if not run_id:
    wandb_dir = Path(block_dir) / "artifacts" / "wandb" / "wandb"
    runs = sorted(wandb_dir.glob("*-run-*"), key=lambda p: p.stat().st_mtime, reverse=True) if wandb_dir.exists() else []
    if runs:
        run_id = runs[0].name.rsplit("-", 1)[-1]
outputs["training_curves"]["value"] = run_id or None

# train_results.json
train_results_path = out / "train_results.json"
if train_results_path.exists():
    with open(train_results_path) as f:
        tr = json.load(f)
    outputs["train_results"]["value"] = str(train_results_path)
    outputs["final_loss"]["value"] = tr.get("train_loss")
    outputs["train_runtime"]["value"] = tr.get("train_runtime")

# trainer_state.json
trainer_state_path = out / "trainer_state.json"
if trainer_state_path.exists():
    with open(trainer_state_path) as f:
        ts = json.load(f)
    outputs["total_steps"]["value"] = ts.get("global_step")

# training_loss.png
loss_plot_path = out / "training_loss.png"
if loss_plot_path.exists():
    outputs["train_loss_plot"]["value"] = str(loss_plot_path)

# Write with section headers and blank lines
SECTIONS = {
    "checkpoint_path": "Model checkpoint",
    "final_loss": "Training metrics",
    "train_results": "Artifacts",
    "training_curves": "Experiment tracking",
}
ORDER = [
    "checkpoint_path",
    "final_loss", "total_steps", "train_runtime",
    "train_results", "train_loss_plot", "training_log",
    "training_curves",
]

lines = ["# Outputs produced by this block. Updated automatically by train.sh after each run.\n"]
for key in ORDER:
    if key not in outputs:
        continue
    if key in SECTIONS:
        lines.append(f"\n# {'─' * 75}")
        lines.append(f"# {SECTIONS[key]}")
        lines.append(f"# {'─' * 75}")
    entry = {key: outputs[key]}
    text = yaml.dump(entry, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip()
    lines.append(text)

with open(outputs_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print(f"=== Updated outputs.yaml ===")
print(f"    checkpoint: {outputs['checkpoint_path']['value']}")
print(f"    wandb_run_id: {outputs['training_curves']['value']}")
print(f"    log: {outputs['training_log']['value']}")
print(f"    final_loss: {outputs['final_loss']['value']}")
print(f"    total_steps: {outputs['total_steps']['value']}")
print(f"    train_runtime: {outputs['train_runtime']['value']}")
PYEOF

echo "=== Training done. Log: $TRAIN_LOG ==="
