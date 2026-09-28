#!/bin/bash
# End-to-end SFT pipeline:
#   STEP 0: Data conversion  (raw trajectories → IM JSONL → LF JSON)
#   STEP 1: Dataset registration  (LF JSON → dataset_info.json)
#   STEP 2: Generate train config and launch SFT training
#
# Reads all runtime config from config.yaml -> runtime_info.input.
# Run from anywhere: bash scripts/train.sh
# Assumes you are already on a compute node with GPUs available.
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve block root and repo paths
# ---------------------------------------------------------------------------
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWE_DP_REPO="$BLOCK_DIR/repos/LegoFlow-Trace-Crafter"
SWE_DP_SRC="$SWE_DP_REPO/src"
# CONFIG defaults to the block's canonical config.yaml. Override with SFT_CONFIG
# to run against an alternate config without touching the canonical one — used by
# the training smoke (tests/smoke/10_train_demo.sh), which points train.sh at a
# disposable copy so STEP 3's runtime_info.output write lands in the copy.
CONFIG="${SFT_CONFIG:-$BLOCK_DIR/config.yaml}"
CONFIG_PYTHON="${CONFIG_PYTHON:-python3}"

abspath() {
    local p="$1"
    if [[ -z "$p" ]]; then echo ""; elif [[ "$p" = /* ]]; then echo "$p"; else echo "$BLOCK_DIR/$p"; fi
}

resolve_output_dir() {
    local p="$1"
    if [[ "$p" = /* ]]; then echo "$p"; else echo "$BLOCK_DIR/artifacts/model/$(basename "$p")"; fi
}

load_cuda_module() {
    local module_name="${CUDA_MODULE:-}"
    if [[ -z "$module_name" ]]; then
        return
    fi
    if command -v module >/dev/null 2>&1; then
        module load "$module_name" || echo "WARNING: failed to load CUDA module '$module_name'; continuing with current environment" >&2
    else
        echo "WARNING: module command not available; skipping CUDA module load" >&2
    fi
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
    echo "  uv pip install --python \"$LF_PYTHON\" -e \"$BLOCK_DIR/repos/LLaMA-Factory\" -e \"$BLOCK_DIR/repos/LegoFlow-Trace-Crafter\""
    exit 1
fi

load_cuda_module

# Parse config.yaml runtime_info.input with the SFT uv environment Python; returns "" for null/None values.
cfg() {
    "$LF_PYTHON" "$BLOCK_DIR/scripts/config_value.py" "$CONFIG" runtime_input "$1" --default "${2:-}"
}

# ---------------------------------------------------------------------------
# Load config values
# ---------------------------------------------------------------------------
SOURCE_TYPE="$(cfg "source.type")"
[[ -z "$SOURCE_TYPE" ]] && SOURCE_TYPE="harbor_job"
SCAFFOLD="$(cfg "source.scaffold")"
JOB_DIR_RAW="$(cfg "source.job_dir")"
JOB_DIR="$(abspath "$JOB_DIR_RAW")"
HF_HUB_URL="$(cfg "source.hf_hub_url")"
HF_FILE_NAME="$(cfg "source.hf_file_name")"
HF_SUBSET="$(cfg "source.hf_subset")"
HF_SPLIT="$(cfg "source.hf_split")"
LF_PATH_RAW="$(cfg "source.lf_path")"
LF_PATH="$(abspath "$LF_PATH_RAW")"
HF_TOKEN_VAL="${HF_TOKEN:-$(cfg "credentials.hf_token")}"

# Make a configured token available both to an exact-file download in STEP 0
# and to LLaMA-Factory's Hub loader in STEP 2. An existing HF_TOKEN remains
# untouched when credentials.hf_token is empty.
if [[ "$SOURCE_TYPE" == "hf_lf" && -n "$HF_TOKEN_VAL" ]]; then
    export HF_TOKEN="$HF_TOKEN_VAL"
    export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN_VAL"
fi

# This pipeline registers text-only ShareGPT messages. Qwen3.5 is
# multimodal-capable, so LLaMA-Factory would otherwise interpret literal
# <image>/<video>/<audio> strings in code, HTML, or Markdown as media inputs.
# Preserve explicit caller overrides while defaulting to collision-resistant
# sentinels that cannot be mistaken for ordinary source text.
export IMAGE_PLACEHOLDER="${IMAGE_PLACEHOLDER:-<|__lf_image_placeholder__|>}"
export VIDEO_PLACEHOLDER="${VIDEO_PLACEHOLDER:-<|__lf_video_placeholder__|>}"
export AUDIO_PLACEHOLDER="${AUDIO_PLACEHOLDER:-<|__lf_audio_placeholder__|>}"

MAX_INSTANCES="$(cfg "conversion.max_instances")"
EXCLUDE_REPOS_RAW="$(cfg "conversion.exclude_repos_file")"
EXCLUDE_REPOS_FILE="$(abspath "$EXCLUDE_REPOS_RAW")"
DATA_NAME="$(cfg "conversion.data_name")"
IM_OUTPUT="$BLOCK_DIR/artifacts/data/im_data/${DATA_NAME}.jsonl"
LF_OUTPUT="$BLOCK_DIR/artifacts/data/lf_data/${DATA_NAME}.json"

DATASET_NAME_RAW="$(cfg "dataset.name")"

MODEL_PATH="$(cfg "model.model_name_or_path")"
TRUST_REMOTE_CODE="$(cfg "model.trust_remote_code")"

STAGE="$(cfg "training.stage")"
FINETUNING_TYPE="$(cfg "training.finetuning_type")"
DEEPSPEED="$(abspath "$(cfg "training.deepspeed")")"
TEMPLATE="$(cfg "training.template")"
CUTOFF_LEN="$(cfg "training.cutoff_len")"
ROPE_SCALING="$(cfg "training.rope_scaling")"
MAX_SAMPLES="$(cfg "training.max_samples")"
PREPROCESSING_WORKERS="$(cfg "training.preprocessing_num_workers")"
DATALOADER_WORKERS="$(cfg "training.dataloader_num_workers")"
OUTPUT_DIR="$(cfg "training.output_dir")"
SAVE_STRATEGY="$(cfg "training.save_strategy")"
LOGGING_STEPS="$(cfg "training.logging_steps")"
SAVE_STEPS="$(cfg "training.save_steps")"
OVERWRITE_OUTPUT_DIR="$(cfg "training.overwrite_output_dir")"
SAVE_ONLY_MODEL="$(cfg "training.save_only_model")"
RESUME_FROM_CHECKPOINT="$(cfg "training.resume_from_checkpoint")"
PER_DEVICE_BATCH="$(cfg "training.per_device_train_batch_size")"
GRAD_ACCUM="$(cfg "training.gradient_accumulation_steps")"
LR="$(cfg "training.learning_rate")"
WEIGHT_DECAY="$(cfg "training.weight_decay")"
MAX_GRAD_NORM="$(cfg "training.max_grad_norm")"
EPOCHS="$(cfg "training.num_train_epochs")"
LR_SCHEDULER="$(cfg "training.lr_scheduler_type")"
WARMUP_RATIO="$(cfg "training.warmup_ratio")"
BF16="$(cfg "training.bf16")"
DDP_TIMEOUT="$(cfg "training.ddp_timeout")"
ENABLE_LIGER="$(cfg "training.enable_liger_kernel")"
USE_UNSLOTH_GC="$(cfg "training.use_unsloth_gc")"
FLASH_ATTN="$(cfg "training.flash_attn")"
RUN_NAME_RAW="$(cfg "experiment.run_name")"
WANDB_API_KEY_VAL="${WANDB_API_KEY:-$(cfg "credentials.wandb_api_key")}"
WANDB_MODE="$(cfg "experiment.wandb_mode")"
WANDB_RUN_ID="$(cfg "experiment.wandb_run_id")"

N_GPUS="$(cfg "infrastructure.n_gpus_per_node")"

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

echo "=== trainer pipeline ==="
echo "    Block:       $BLOCK_DIR"
echo "    Source type: $SOURCE_TYPE"
case "$SOURCE_TYPE" in
    harbor_job)
        echo "    Scaffold:    $SCAFFOLD"
        echo "    LF output:   $LF_OUTPUT"
        ;;
    hf_lf)
        echo "    HF dataset:  $HF_HUB_URL${HF_FILE_NAME:+/$HF_FILE_NAME}${HF_SUBSET:+ (subset=$HF_SUBSET)}${HF_SPLIT:+ split=$HF_SPLIT}"
        ;;
    local_lf)   echo "    LF path:     $LF_PATH" ;;
esac
echo "    Dataset:     $DATASET_NAME"
echo "    Output dir:  $OUTPUT_DIR"

if [[ -z "$DATA_NAME" ]]; then
    echo "ERROR: conversion.data_name is empty — set runtime_info.input.conversion.data_name in config.yaml"
    exit 1
fi

if ! [[ "$N_GPUS" =~ ^[0-9]+$ ]] || [[ "$N_GPUS" -lt 1 ]]; then
    echo "ERROR: infrastructure.n_gpus_per_node must be a positive integer"
    exit 1
fi

if [[ -z "$RUN_NAME" ]]; then
    RUN_NAME="$DATASET_NAME"
fi

# ---------------------------------------------------------------------------
# STEP 0: Obtain the LF dataset, depending on source.type.
#   harbor_job — convert raw Harbor trajectories (IM -> LF), idempotent.
#   hf_lf      — load a Hub dataset, or download/register one exact file when
#                source.hf_file_name is set.
#   local_lf   — no conversion; an existing LF json is registered as-is.
# Sets REGISTER_MODE (file|hf_hub) and REGISTER_VALUE for STEP 1.
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "STEP 0: Obtain LF dataset (source.type=$SOURCE_TYPE)"
echo "============================================================"

case "$SOURCE_TYPE" in
    harbor_job)
        if [[ -z "$JOB_DIR" ]]; then
            echo "ERROR: source.job_dir is empty — set runtime_info.input.source.job_dir in config.yaml"
            exit 1
        fi
        case "${SCAFFOLD}" in
            openhands-sdk) CONVERTER_MODULE="legoflow_trace_crafter.openhands.convert_openhands_sdk_to_im" ;;
            claude-code)   CONVERTER_MODULE="legoflow_trace_crafter.claudecode_opencode.convert_cc_to_im" ;;
            open-code)     CONVERTER_MODULE="legoflow_trace_crafter.claudecode_opencode.convert_oc_to_im" ;;
            terminus2)     CONVERTER_MODULE="legoflow_trace_crafter.terminus2.convert_terminus2_to_im" ;;
            *)
                echo "ERROR: Unsupported scaffold for job-dir conversion: '${SCAFFOLD}'"
                echo "  Valid scaffold: openhands-sdk | claude-code | open-code | terminus2"
                exit 1
                ;;
        esac

        CONVERT_ARGS=(--job-dir "$JOB_DIR" --im-output "$IM_OUTPUT" --lf-output "$LF_OUTPUT")
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
        REGISTER_MODE="file"
        REGISTER_VALUE="$(basename "$LF_OUTPUT")"
        ;;

    local_lf)
        if [[ -z "$LF_PATH" ]]; then
            echo "ERROR: source.lf_path is empty — set runtime_info.input.source.lf_path for source.type=local_lf"
            exit 1
        fi
        if [[ ! -f "$LF_PATH" ]]; then
            echo "ERROR: source.lf_path not found: $LF_PATH"
            exit 1
        fi
        echo "=== Using existing local LF dataset (no conversion): $LF_PATH ==="
        REGISTER_MODE="file"
        REGISTER_VALUE="$LF_PATH"
        ;;

    hf_lf)
        if [[ -z "$HF_HUB_URL" ]]; then
            echo "ERROR: source.hf_hub_url is empty — set runtime_info.input.source.hf_hub_url for source.type=hf_lf"
            exit 1
        fi
        if [[ -n "$HF_FILE_NAME" ]]; then
            HF_DOWNLOAD_DIR="$BLOCK_DIR/artifacts/data/hf_data/${HF_HUB_URL//\//__}"
            mkdir -p "$HF_DOWNLOAD_DIR"
            echo "=== Downloading exact LF file from the HuggingFace Hub ==="
            echo "    repo: $HF_HUB_URL"
            echo "    file: $HF_FILE_NAME"
            REGISTER_VALUE="$(
                "$LF_PYTHON" - "$HF_HUB_URL" "$HF_FILE_NAME" "$HF_DOWNLOAD_DIR" <<'PYEOF'
import sys
from huggingface_hub import hf_hub_download

repo_id, filename, local_dir = sys.argv[1:4]
path = hf_hub_download(
    repo_id=repo_id,
    filename=filename,
    repo_type="dataset",
    local_dir=local_dir,
)
print(path)
PYEOF
            )"
            echo "=== Exact LF file ready: $REGISTER_VALUE ==="
            REGISTER_MODE="file"
        else
            echo "=== Dataset will be loaded from the HuggingFace Hub at train time (no conversion) ==="
            echo "    hf_hub_url: $HF_HUB_URL${HF_SUBSET:+  subset: $HF_SUBSET}${HF_SPLIT:+  split: $HF_SPLIT}"
            REGISTER_MODE="hf_hub"
            REGISTER_VALUE="$HF_HUB_URL"
        fi
        ;;

    *)
        echo "ERROR: Unsupported source.type '$SOURCE_TYPE' — valid: harbor_job | hf_lf | local_lf"
        exit 1
        ;;
esac

# For ready-made LF sources, cap the loaded rows via the dataset's num_samples
# (harbor_job already applies max_instances during conversion).
ENTRY_NUM_SAMPLES=""
if [[ "$SOURCE_TYPE" != "harbor_job" ]] && [[ -n "$MAX_INSTANCES" ]] && [[ "$MAX_INSTANCES" -gt 0 ]] 2>/dev/null; then
    ENTRY_NUM_SAMPLES="$MAX_INSTANCES"
fi

# ---------------------------------------------------------------------------
# STEP 1: Dataset registration in dataset_info.json (idempotent)
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "STEP 1: Dataset registration"
echo "============================================================"

DATASET_INFO="$BLOCK_DIR/artifacts/data/lf_data/dataset_info.json"

mkdir -p "$BLOCK_DIR/artifacts/data/lf_data"
if [[ ! -f "$DATASET_INFO" ]]; then
    echo '{}' > "$DATASET_INFO"
fi

"$LF_PYTHON" - "$DATASET_INFO" "$DATASET_NAME" "$REGISTER_MODE" "$REGISTER_VALUE" "$HF_SUBSET" "$HF_SPLIT" "$ENTRY_NUM_SAMPLES" <<'PYEOF'
import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path

dataset_info_path, dataset_name, mode, value, subset, split, num_samples = sys.argv[1:8]
path = Path(dataset_info_path)
lock_path = path.with_suffix(path.suffix + ".lock")

common = {
    "formatting": "sharegpt",
    "columns": {"messages": "messages"},
    "tags": {
        "role_tag": "role",
        "content_tag": "content",
        "user_tag": "user",
        "assistant_tag": "assistant",
        "system_tag": "system",
    },
}

if mode == "hf_hub":
    desired_entry = {"hf_hub_url": value}
    if subset:
        desired_entry["subset"] = subset
    if split and split != "train":
        desired_entry["split"] = split
    desired_entry.update(common)
    src_label = f"hf_hub_url={value}"
else:  # file (local LF json or converted harbor output)
    desired_entry = {"file_name": value}
    desired_entry.update(common)
    src_label = f"file_name={value}"

if num_samples:
    try:
        n = int(num_samples)
        if n > 0:
            desired_entry["num_samples"] = n
    except ValueError:
        pass

with open(lock_path, "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)

    with open(path, encoding="utf-8") as f:
        info = json.load(f)

    if info.get(dataset_name) == desired_entry:
        print(f"=== Dataset '{dataset_name}' already registered ({src_label}) — skipping ===")
    else:
        old_entry = info.get(dataset_name)
        info[dataset_name] = desired_entry
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
            json.dump(info, tmp, indent=4, ensure_ascii=False)
            tmp.write("\n")
            tmp_path = tmp.name
        os.replace(tmp_path, path)
        if old_entry is None:
            print(f"=== Registered dataset '{dataset_name}' -> {src_label} ===")
        else:
            old_src = old_entry.get("hf_hub_url") or old_entry.get("file_name")
            print(f"=== Updated dataset '{dataset_name}': {old_src} -> {src_label} ===")
PYEOF

# ---------------------------------------------------------------------------
# STEP 2: Generate LLaMA-Factory train YAML and launch training
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "STEP 2: Generate train config and launch training"
echo "============================================================"

if [[ -z "$OUTPUT_DIR" ]]; then
    echo "ERROR: training.output_dir is empty — set runtime_info.input.training.output_dir in config.yaml"
    exit 1
fi

# Relative output dirs are stored under artifacts/model/; absolute paths are honored.
ABS_OUTPUT_DIR="$(resolve_output_dir "$OUTPUT_DIR")"
if [[ -d "$ABS_OUTPUT_DIR" && -n "$(find "$ABS_OUTPUT_DIR" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
    if [[ -n "$RESUME_FROM_CHECKPOINT" && "$RESUME_FROM_CHECKPOINT" != "null" ]]; then
        echo "INFO: resuming existing output from checkpoint: $RESUME_FROM_CHECKPOINT"
    elif [[ "$OVERWRITE_OUTPUT_DIR" == "true" && "${SFT_ALLOW_OVERWRITE_OUTPUT:-0}" == "1" ]]; then
        echo "WARNING: destructive overwrite explicitly enabled for $ABS_OUTPUT_DIR" >&2
    else
        echo "ERROR: refusing to train into non-empty output_dir: $ABS_OUTPUT_DIR" >&2
        echo "       Choose a new training.output_dir. To overwrite intentionally, set" >&2
        echo "       overwrite_output_dir=true and SFT_ALLOW_OVERWRITE_OUTPUT=1." >&2
        exit 1
    fi
fi

# Generate the LLaMA-Factory train YAML from config.yaml runtime_info.input parameters
TRAIN_YAML_NAME="$(basename "$OUTPUT_DIR").yaml"
TRAIN_YAML_PATH="$BLOCK_DIR/artifacts/training_config/$TRAIN_YAML_NAME"

echo "=== Generating train config: artifacts/training_config/$TRAIN_YAML_NAME ==="
mkdir -p "$BLOCK_DIR/artifacts/training_config"

if [[ "$WANDB_MODE" == "disabled" ]]; then
    REPORT_TO="none"
else
    REPORT_TO="wandb"
fi

"$LF_PYTHON" - "$CONFIG" "$TRAIN_YAML_PATH" "$BLOCK_DIR" "$DEEPSPEED" "$DATASET_NAME" "$ABS_OUTPUT_DIR" "$RUN_NAME" "$REPORT_TO" <<'PYEOF'
import os
import sys
import tempfile
from pathlib import Path

import yaml

config_path, train_yaml_path, block_dir, deepspeed, dataset_name, output_dir, run_name, report_to = sys.argv[1:9]

with open(config_path, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh)["runtime_info"]["input"]

model = cfg["model"]
training = cfg["training"]
resume = training.get("resume_from_checkpoint")
if resume in ("", "None", "null"):
    resume = None

data = {
    "model_name_or_path": model["model_name_or_path"],
    "trust_remote_code": model["trust_remote_code"],
    "stage": training["stage"],
    "do_train": True,
    "finetuning_type": training["finetuning_type"],
    "deepspeed": deepspeed,
    "dataset_dir": str(Path(block_dir) / "artifacts" / "data" / "lf_data"),
    "dataset": dataset_name,
    "template": training["template"],
    "cutoff_len": training["cutoff_len"],
    "max_samples": training["max_samples"],
    "overwrite_cache": True,
    "preprocessing_num_workers": training["preprocessing_num_workers"],
    "dataloader_num_workers": training["dataloader_num_workers"],
    "output_dir": output_dir,
    "run_name": run_name,
    "logging_steps": training["logging_steps"],
    "save_steps": training["save_steps"],
    "save_strategy": training["save_strategy"],
    "plot_loss": True,
    "overwrite_output_dir": training["overwrite_output_dir"],
    "save_only_model": training["save_only_model"],
    "report_to": report_to,
    "resume_from_checkpoint": resume,
    "per_device_train_batch_size": training["per_device_train_batch_size"],
    "gradient_accumulation_steps": training["gradient_accumulation_steps"],
    "learning_rate": training["learning_rate"],
    "weight_decay": training["weight_decay"],
    "max_grad_norm": training["max_grad_norm"],
    "num_train_epochs": training["num_train_epochs"],
    "lr_scheduler_type": training["lr_scheduler_type"],
    # (max_steps injected below when set — HF caps training at this many
    #  optimizer steps and ignores num_train_epochs; default config omits it)
    "warmup_ratio": training["warmup_ratio"],
    "bf16": training["bf16"],
    "ddp_timeout": training["ddp_timeout"],
    "enable_liger_kernel": training["enable_liger_kernel"],
    "use_unsloth_gc": training["use_unsloth_gc"],
    "flash_attn": training["flash_attn"],
}
if training.get("rope_scaling"):
    data["rope_scaling"] = training["rope_scaling"]

# Optional hard cap on optimizer steps. Only emitted when training.max_steps is
# present and > 0 (default config omits it, so full-length runs are unaffected).
# HF Trainer treats max_steps > 0 as authoritative over num_train_epochs.
_max_steps = training.get("max_steps")
try:
    _max_steps = int(_max_steps) if _max_steps not in (None, "", "None", "null") else 0
except (TypeError, ValueError):
    _max_steps = 0
if _max_steps > 0:
    data["max_steps"] = _max_steps

# save_strategy "no" disables checkpoint writes entirely — train_results.json /
# trainer_state.json / training_loss.png are still produced at run end.
if str(training.get("save_strategy", "")).lower() in ("no", "none"):
    data["save_strategy"] = "no"
    data.pop("save_steps", None)

path = Path(train_yaml_path)
with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
    yaml.safe_dump(data, tmp, allow_unicode=True, sort_keys=False)
    tmp_path = tmp.name
os.replace(tmp_path, path)
PYEOF

echo "    Written: $TRAIN_YAML_PATH"

mkdir -p "$BLOCK_DIR/artifacts/logs"
TRAIN_LOG="$BLOCK_DIR/artifacts/logs/${RUN_NAME}_$(date +%Y%m%d_%H%M%S).log"

WANDB_MODE_CFG="$WANDB_MODE"
mkdir -p "$BLOCK_DIR/artifacts"
export WANDB_DIR="$BLOCK_DIR/artifacts"
case "$WANDB_MODE_CFG" in
    online)
        if [[ -z "$WANDB_API_KEY_VAL" ]]; then
            echo "ERROR: WANDB_API_KEY is required when experiment.wandb_mode=online"
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

# Live progress is served by the dashboard webui (dashboard/start_dashboard.sh),
# which reads trainer_log.jsonl from artifacts/model/<run>/ directly.
# TORCHELASTIC_ERROR_FILE: without it torchrun reports every rank as
# "error_file: <N/A>" and the worker's actual exception is never written
# anywhere — a failed run leaves only "CalledProcessError: Command ['torchrun'
# ...] returned non-zero exit status 1", which says nothing about why. The
# per-rank JSON below is what makes a training failure diagnosable at all.
TORCHELASTIC_ERROR_DIR="$BLOCK_DIR/artifacts/logs/torchelastic"
mkdir -p "$TORCHELASTIC_ERROR_DIR"
export TORCHELASTIC_ERROR_FILE="$TORCHELASTIC_ERROR_DIR/rank.json"
FORCE_TORCHRUN=1 NPROC_PER_NODE="$N_GPUS" PYTHONPATH="$BLOCK_DIR/repos/LLaMA-Factory/src:${PYTHONPATH:-}" PATH="$SFT_UV/bin:$PATH" "$LF_PYTHON" -m llamafactory.cli train "$TRAIN_YAML_PATH" 2>&1 | tee "$TRAIN_LOG"
_TRAIN_RC="${PIPESTATUS[0]}"
if [[ "$_TRAIN_RC" -ne 0 ]]; then
    echo "=== torchrun failed (rc=$_TRAIN_RC) — per-rank error files ==="
    for _ef in "$TORCHELASTIC_ERROR_DIR"/*.json; do
        [[ -f "$_ef" ]] || continue
        echo "--- $_ef"
        python3 -c "import json,sys;d=json.load(open(sys.argv[1]));m=(d.get('message') or {});print(m.get('message') or d)[:2000]" "$_ef" 2>/dev/null \
            || head -c 2000 "$_ef"
    done
fi

# ---------------------------------------------------------------------------
# STEP 3: Update config.yaml runtime_info.output
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "STEP 3: Update config.yaml runtime_info.output"
echo "============================================================"
"$LF_PYTHON" - "$BLOCK_DIR" "$ABS_OUTPUT_DIR" "$TRAIN_LOG" "$CONFIG" <<'PYEOF'
import fcntl
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

# config_path comes from $CONFIG (honors the SFT_CONFIG override) so a smoke /
# alt run writes runtime_info.output into ITS config, never the canonical one.
block_dir, output_dir, train_log, config_path_arg = sys.argv[1:5]
config_path = Path(config_path_arg)
# The fcntl sidecar is scratch state, so it belongs under artifacts/, not next
# to the config in the block root. Name it after the config so an alternate
# config (smoke / SFT_CONFIG override) still gets its own distinct lock.
lock_dir = Path(block_dir) / "artifacts"
lock_dir.mkdir(parents=True, exist_ok=True)
lock_path = lock_dir / (config_path.name + ".lock")


def find_wandb_run_id(block_dir: Path) -> str:
    candidates = []
    for root in (block_dir / "artifacts" / "wandb", block_dir / "artifacts" / "wandb" / "wandb"):
        if root.exists():
            candidates.extend(p for p in root.iterdir() if p.is_dir() and "run-" in p.name)
    if not candidates:
        return ""
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return latest.name.rsplit("-", 1)[-1]


def dump_output_block(output: dict) -> str:
    dumped = yaml.safe_dump(
        {"output": output},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return "\n".join("  " + line if line else line for line in dumped.rstrip("\n").splitlines()) + "\n"


def write_runtime_output_preserving_comments(path: Path, output: dict) -> None:
    original = path.read_text(encoding="utf-8")
    output_block = dump_output_block(output)
    match = re.search(r"(?ms)^  output:\n.*?(?=^[^ \n]|\Z)", original)
    if match:
        updated = original[:match.start()] + output_block + original[match.end():]
    else:
        runtime_match = re.search(r"(?m)^runtime_info:\n", original)
        if not runtime_match:
            raise ValueError("config.yaml is missing runtime_info")
        insert_at = runtime_match.end()
        updated = original[:insert_at] + output_block + original[insert_at:]

    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
        tmp.write(updated)
        tmp_path = tmp.name
    os.replace(tmp_path, path)

with open(lock_path, "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    runtime_info = config.setdefault("runtime_info", {})
    outputs = runtime_info.setdefault("output", {})

    # checkpoint_path: find the latest checkpoint-* subdir, or use output_dir itself
    out = Path(output_dir)
    ckpts = sorted(out.glob("checkpoint-*"), key=lambda p: p.stat().st_mtime, reverse=True) if out.exists() else []
    checkpoint = str(ckpts[0]) if ckpts else str(out)
    checkpoint_entry = outputs.setdefault("checkpoint_path", {})
    if not isinstance(checkpoint_entry, dict):
        checkpoint_entry = {}
        outputs["checkpoint_path"] = checkpoint_entry
    checkpoint_entry.setdefault("description", "Path to the latest trained model checkpoint")
    checkpoint_entry["value"] = checkpoint

    # training_log
    artifacts = outputs.setdefault("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
        outputs["artifacts"] = artifacts
    artifacts["training_log"] = train_log

    # wandb_run_id: read from WANDB_RUN_ID env or scan wandb dir
    run_id = os.environ.get("WANDB_RUN_ID", "")
    if not run_id:
        run_id = find_wandb_run_id(Path(block_dir))
    training_curves = outputs.setdefault("training_curves", {})
    if not isinstance(training_curves, dict):
        training_curves = {}
        outputs["training_curves"] = training_curves
    training_curves.setdefault("description", "WandB run id with loss, learning rate, and token metrics")
    training_curves["value"] = run_id or None

    training_metrics = outputs.setdefault("training_metrics", {})
    if not isinstance(training_metrics, dict):
        training_metrics = {}
        outputs["training_metrics"] = training_metrics
    training_metrics.setdefault("description", "Final training metrics from LLaMA-Factory")
    metrics_value = training_metrics.setdefault("value", {})
    if not isinstance(metrics_value, dict):
        metrics_value = {}
        training_metrics["value"] = metrics_value

    # train_results.json
    train_results_path = out / "train_results.json"
    if train_results_path.exists():
        with open(train_results_path) as f:
            tr = json.load(f)
        artifacts["train_results"] = str(train_results_path)
        metrics_value["final_loss"] = tr.get("train_loss")
        metrics_value["train_runtime"] = tr.get("train_runtime")

    # trainer_state.json
    trainer_state_path = out / "trainer_state.json"
    if trainer_state_path.exists():
        with open(trainer_state_path) as f:
            ts = json.load(f)
        metrics_value["total_steps"] = ts.get("global_step")

    # training_loss.png
    loss_plot_path = out / "training_loss.png"
    if loss_plot_path.exists():
        artifacts["train_loss_plot"] = str(loss_plot_path)

    write_runtime_output_preserving_comments(config_path, outputs)

print("=== Updated config.yaml runtime_info.output ===")
print(f"    checkpoint: {checkpoint_entry['value']}")
print(f"    wandb_run_id: {training_curves['value']}")
print(f"    log: {artifacts.get('training_log')}")
print(f"    final_loss: {metrics_value.get('final_loss')}")
print(f"    total_steps: {metrics_value.get('total_steps')}")
print(f"    train_runtime: {metrics_value.get('train_runtime')}")
PYEOF

echo "=== Training done. Log: $TRAIN_LOG ==="
