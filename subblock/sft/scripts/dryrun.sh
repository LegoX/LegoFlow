#!/bin/bash
# Validate config, paths, converter modules, and environment without running training.
# Run from anywhere: bash scripts/dryrun.sh
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LF_REPO="$BLOCK_DIR/repos/LLaMA-Factory"
SWE_DP_REPO="$BLOCK_DIR/repos/swe_data_process"
SWE_DP_SRC="$SWE_DP_REPO/src"
CONFIG="$BLOCK_DIR/config.yaml"
CONFIG_PYTHON="${CONFIG_PYTHON:-python3}"

PASS=0
FAIL=0
WARN=0

ok()   { echo "  [OK]   $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $1"; WARN=$((WARN+1)); }
info() { echo "  [INFO] $1"; }

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
        info "CUDA module loading disabled; set CUDA_MODULE=<module> to load one"
    elif command -v module >/dev/null 2>&1; then
        if module load "$module_name"; then
            ok "Loaded CUDA module: $module_name"
        else
            warn "Failed to load CUDA module '$module_name'; current environment will be checked"
        fi
    else
        warn "module command not available; current environment will be checked"
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

cfg() {
    "$CONFIG_PYTHON" "$BLOCK_DIR/scripts/config_value.py" "$CONFIG" runtime_input "$1" --default "${2:-}"
}

converter_module_for_scaffold() {
    case "$1" in
        openhands-sdk) echo "swe_data_process.openhands.convert_openhands_sdk_to_im" ;;
        claude-code)   echo "swe_data_process.claudecode_opencode.convert_cc_to_im" ;;
        open-code)     echo "swe_data_process.claudecode_opencode.convert_oc_to_im" ;;
        terminus2)     echo "swe_data_process.terminus2.convert_terminus2_to_im" ;;
        *)             echo "" ;;
    esac
}

echo "=== sft dryrun: $BLOCK_DIR ==="
echo ""
load_cuda_module
echo ""

# ---------------------------------------------------------------------------
# 1. config.yaml
# ---------------------------------------------------------------------------
echo "--- 1. Config file ---"
if [[ -f "$CONFIG" ]]; then
    ok "config.yaml exists"
else
    fail "config.yaml not found"
    exit 1
fi

if [[ -x "$LF_PYTHON" ]]; then
    ok "SFT uv python exists at $LF_PYTHON"
else
    fail "SFT uv python not found at $LF_PYTHON"
fi

if "$CONFIG_PYTHON" - "$CONFIG" <<'PYEOF' 2>/dev/null
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as fh:
    config = yaml.safe_load(fh)

assert isinstance(config, dict)
assert isinstance(config["runtime_info"]["input"], dict)
PYEOF
then
    ok "config.yaml is valid YAML with runtime_info.input"
else
    fail "config.yaml must contain runtime_info.input"
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. uv environment
# ---------------------------------------------------------------------------
echo ""
echo "--- 2. uv environment ---"
if command -v uv &>/dev/null; then
    ok "uv command is available"
else
    fail "uv command not found"
fi

ok "SFT uv environment path = $SFT_UV_RAW"
if [[ -d "$SFT_UV" ]]; then
    ok "SFT uv environment exists"
else
    fail "SFT uv environment is missing; create it with: uv venv $SFT_UV --python ${SFT_PYTHON_VERSION:-3.12} && uv pip install --python $LF_PYTHON -e $LF_REPO -e $SWE_DP_REPO"
fi

if [[ -x "$LF_PYTHON" ]]; then
    ok "SFT uv python is executable"
else
    fail "SFT uv python is missing: $LF_PYTHON"
fi

# ---------------------------------------------------------------------------
# 3. Repos and imports
# ---------------------------------------------------------------------------
echo ""
echo "--- 3. Repos and Python modules ---"
if [[ -d "$LF_REPO" ]]; then
    ok "repos/LLaMA-Factory/ exists"
else
    fail "repos/LLaMA-Factory/ not found"
fi

if [[ -f "$SWE_DP_REPO/pyproject.toml" && -d "$SWE_DP_SRC/swe_data_process" ]]; then
    ok "repos/swe_data_process is an installable src-layout package"
else
    fail "repos/swe_data_process package files not found"
fi

if [[ -x "$LF_PYTHON" ]] && PYTHONPATH="$SWE_DP_SRC:${PYTHONPATH:-}" "$LF_PYTHON" -c "import swe_data_process" 2>/dev/null; then
    ok "swe_data_process is importable with local PYTHONPATH"
else
    warn "skipping or failing swe_data_process import check because SFT uv python is unavailable or import failed"
fi

# ---------------------------------------------------------------------------
# 4. Source data
# ---------------------------------------------------------------------------
echo ""
echo "--- 4. Source data ---"
SCAFFOLD="$(cfg "source.scaffold")"
JOB_DIR_RAW="$(cfg "source.job_dir")"
JOB_DIR="$(abspath "$JOB_DIR_RAW")"

info "scaffold=$SCAFFOLD"

CONVERTER_MODULE="$(converter_module_for_scaffold "$SCAFFOLD")"
if [[ -n "$CONVERTER_MODULE" ]]; then
    ok "scaffold is supported: $SCAFFOLD"
else
    fail "Unsupported scaffold '$SCAFFOLD'. Valid: openhands-sdk claude-code open-code terminus2"
fi

if [[ -z "$JOB_DIR" ]]; then
    fail "source.job_dir is empty — set runtime_info.input.source.job_dir"
elif [[ -d "$JOB_DIR" ]]; then
    ok "source.job_dir exists: $JOB_DIR"
else
    warn "source.job_dir not found (may be on another node): $JOB_DIR"
fi

# ---------------------------------------------------------------------------
# 5. Converter module
# ---------------------------------------------------------------------------
echo ""
echo "--- 5. Converter module ---"
if [[ -n "$CONVERTER_MODULE" ]]; then
    if [[ ! -x "$LF_PYTHON" ]]; then
        warn "skipping converter module import check because SFT uv python is unavailable"
    elif PYTHONPATH="$SWE_DP_SRC:${PYTHONPATH:-}" "$LF_PYTHON" - "$CONVERTER_MODULE" <<'PYEOF' 2>/dev/null
import importlib.util
import sys

module = sys.argv[1]
raise SystemExit(0 if importlib.util.find_spec(module) else 1)
PYEOF
    then
        ok "Converter module exists: $CONVERTER_MODULE"
    else
        fail "Converter module not found: $CONVERTER_MODULE"
    fi
fi

# ---------------------------------------------------------------------------
# 6. Conversion output paths
# ---------------------------------------------------------------------------
echo ""
echo "--- 6. Conversion output paths ---"
DATA_NAME="$(cfg "conversion.data_name")"
IM_OUTPUT="$BLOCK_DIR/artifacts/data/im_data/${DATA_NAME}.jsonl"
LF_OUTPUT="$BLOCK_DIR/artifacts/data/lf_data/${DATA_NAME}.json"

if [[ -z "$DATA_NAME" ]]; then
    fail "conversion.data_name is empty"
else
    ok "conversion.data_name = $DATA_NAME"
    info "im_output = $IM_OUTPUT"
    info "lf_output = $LF_OUTPUT"
    if [[ -f "$IM_OUTPUT" && -f "$LF_OUTPUT" ]]; then
        COUNT=$("$CONFIG_PYTHON" -c 'import json, sys; print(len(json.load(open(sys.argv[1], encoding="utf-8"))))' "$LF_OUTPUT" 2>/dev/null || echo "?")
        info "IM + LF output both exist ($COUNT LF records) — STEP 0 will be skipped"
    elif [[ -f "$IM_OUTPUT" || -f "$LF_OUTPUT" ]]; then
        fail "Partial conversion output exists; delete or restore the missing IM/LF pair before running conversion"
    fi
fi

EXCL_RAW="$(cfg "conversion.exclude_repos_file")"
EXCL="$(abspath "$EXCL_RAW")"
if [[ -z "$EXCL_RAW" ]]; then
    warn "conversion.exclude_repos_file is empty — repo filtering disabled"
elif [[ -f "$EXCL" ]]; then
    COUNT=$(awk 'NF && $1 !~ /^#/' "$EXCL" | wc -l)
    ok "exclude_repos_file exists ($COUNT repos): $EXCL"
else
    fail "exclude_repos_file not found: $EXCL"
fi

# ---------------------------------------------------------------------------
# 7. Dataset name
# ---------------------------------------------------------------------------
echo ""
echo "--- 7. Dataset registration ---"
DATASET_NAME_RAW="$(cfg "dataset.name")"
if [[ -n "$DATASET_NAME_RAW" ]]; then
    DATASET_NAME="$DATASET_NAME_RAW"
else
    DATASET_NAME="$DATA_NAME"
    info "dataset.name not set — auto-derived from data_name: $DATASET_NAME"
fi

if [[ -z "$DATASET_NAME" || "$DATASET_NAME" == "." ]]; then
    fail "Could not derive dataset name — set conversion.data_name or dataset.name"
else
    ok "dataset.name = $DATASET_NAME"
    DATASET_INFO="$BLOCK_DIR/artifacts/data/lf_data/dataset_info.json"
    if [[ -f "$DATASET_INFO" ]]; then
        LF_FILENAME="$(basename "$LF_OUTPUT")"
        DATASET_STATUS=$("$CONFIG_PYTHON" - "$DATASET_INFO" "$DATASET_NAME" "$LF_FILENAME" <<'PYEOF'
import json
import sys

info_path, dataset_name, lf_filename = sys.argv[1:4]

with open(info_path, encoding="utf-8") as fh:
    info = json.load(fh)

entry = info.get(dataset_name)
if entry is None:
    print("missing")
elif entry.get("file_name") == lf_filename:
    print("matched")
else:
    print(f"mismatch:{entry.get('file_name')}")
PYEOF
)
        case "$DATASET_STATUS" in
            matched)
                info "Dataset '$DATASET_NAME' already points to $LF_FILENAME — STEP 1 will be skipped"
                ;;
            mismatch:*)
                warn "Dataset '$DATASET_NAME' currently points to ${DATASET_STATUS#mismatch:}; STEP 1 will update it to $LF_FILENAME"
                ;;
            missing)
                info "Dataset '$DATASET_NAME' not yet registered — STEP 1 will register it"
                ;;
        esac
    else
        info "dataset_info.json not found at $DATASET_INFO — STEP 1 will create it"
    fi
fi

# ---------------------------------------------------------------------------
# 8. Model path
# ---------------------------------------------------------------------------
echo ""
echo "--- 8. Model path ---"
MODEL_PATH="$(cfg "model.model_name_or_path")"
info "model_name_or_path = $MODEL_PATH"
if [[ -n "$MODEL_PATH" && -d "$MODEL_PATH" ]]; then
    ok "Model directory exists"
else
    fail "Model directory not found: $MODEL_PATH"
fi

# ---------------------------------------------------------------------------
# 9. Training output_dir
# ---------------------------------------------------------------------------
echo ""
echo "--- 9. Training config ---"
OUTPUT_DIR="$(cfg "training.output_dir")"
TEMPLATE="$(cfg "training.template")"
EPOCHS="$(cfg "training.num_train_epochs")"
LR="$(cfg "training.learning_rate")"
info "template=$TEMPLATE  epochs=$EPOCHS  lr=$LR"
if [[ -z "$OUTPUT_DIR" ]]; then
    fail "training.output_dir is empty — set it (e.g. qwen3_8b_oh_sdk_1k_gbs64pbs1acc8_lr1e-4_epo4_think)"
else
    ok "training.output_dir = $OUTPUT_DIR"
    ABS_OUTPUT_DIR="$(resolve_output_dir "$OUTPUT_DIR")"
    TRAIN_YAML_NAME="$(basename "$OUTPUT_DIR").yaml"
    info "train YAML will be generated at: artifacts/training_config/$TRAIN_YAML_NAME"
    info "model checkpoints will be saved to: $ABS_OUTPUT_DIR"
    if [[ -f "$BLOCK_DIR/artifacts/training_config/$TRAIN_YAML_NAME" ]]; then
        info "YAML already exists from a previous run — will be overwritten"
    fi
fi

# ---------------------------------------------------------------------------
# 10. WandB key
# ---------------------------------------------------------------------------
echo ""
echo "--- 10. Credentials ---"
WANDB_MODE="$(cfg "experiment.wandb_mode")"
WANDB_KEY="$(cfg "credentials.wandb_api_key")"
case "$WANDB_MODE" in
    online)
        if [[ -n "$WANDB_KEY" ]]; then
            ok "credentials.wandb_api_key is set for online WandB logging"
        else
            fail "credentials.wandb_api_key is empty — required when experiment.wandb_mode=online"
        fi
        ;;
    offline)
        ok "experiment.wandb_mode=offline"
        ;;
    disabled)
        ok "experiment.wandb_mode=disabled"
        info "WandB logging will be disabled in the generated train YAML"
        ;;
    *)
        fail "experiment.wandb_mode must be one of: online, offline, disabled"
        ;;
esac

RUN_NAME_RAW="$(cfg "experiment.run_name")"
if [[ -n "$RUN_NAME_RAW" ]]; then
    RUN_NAME="$RUN_NAME_RAW"
    ok "experiment.run_name = $RUN_NAME (explicit)"
else
    RUN_NAME="$(basename "$OUTPUT_DIR")"
    ok "experiment.run_name = $RUN_NAME (auto-derived from output_dir basename)"
fi

# ---------------------------------------------------------------------------
# 11. GPU availability
# ---------------------------------------------------------------------------
echo ""
echo "--- 11. GPU ---"
N_GPUS="$(cfg "infrastructure.n_gpus_per_node")"
info "n_gpus_per_node = $N_GPUS"
if ! [[ "$N_GPUS" =~ ^[0-9]+$ ]] || [[ "$N_GPUS" -lt 1 ]]; then
    fail "infrastructure.n_gpus_per_node must be a positive integer"
fi
if command -v nvidia-smi &>/dev/null; then
    GPU_COUNT=$(nvidia-smi --list-gpus 2>/dev/null | wc -l || echo 0)
    if [[ "$GPU_COUNT" -ge "$N_GPUS" ]]; then
        ok "nvidia-smi found $GPU_COUNT GPU(s) (need $N_GPUS)"
    else
        warn "nvidia-smi found $GPU_COUNT GPU(s), config expects $N_GPUS"
    fi
else
    fail "nvidia-smi not found — are you on a compute node?"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "================================================="
echo "  PASS: $PASS   WARN: $WARN   FAIL: $FAIL"
echo "================================================="
if [[ $FAIL -gt 0 ]]; then
    echo "Fix the above failures before running scripts/start.sh"
    exit 1
else
    echo "All checks passed. Ready to run: bash scripts/start.sh"
fi
