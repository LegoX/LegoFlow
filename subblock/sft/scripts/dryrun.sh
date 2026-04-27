#!/bin/bash
# Validate config, paths, and environment without running training.
# Run from anywhere: bash scripts/dryrun.sh
set -euo pipefail

module load cuda12.4/toolkit/12.4.1

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LF_REPO="$BLOCK_DIR/repos/LLaMA-Factory"
SWE_DP_SRC="$BLOCK_DIR/repos/swe_data_process/src/swe_data_process"
CONFIG="$BLOCK_DIR/inputs.yaml"
LF_PYTHON="/anaconda3/envs/swelf/bin/python3"

PASS=0
FAIL=0
WARN=0

ok()   { echo "  [OK]   $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $1"; WARN=$((WARN+1)); }
info() { echo "  [INFO] $1"; }

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

echo "=== sft-train dryrun: $BLOCK_DIR ==="
echo ""

# ---------------------------------------------------------------------------
# 1. inputs.yaml
# ---------------------------------------------------------------------------
echo "--- 1. Config file ---"
if [[ -f "$CONFIG" ]]; then
    ok "inputs.yaml exists"
    if [[ -x "$LF_PYTHON" ]]; then
        ok "swelf python exists at $LF_PYTHON"
    else
        fail "swelf python not found at $LF_PYTHON"
    fi
    if "$LF_PYTHON" -c "import yaml; yaml.safe_load(open('$CONFIG'))" 2>/dev/null; then
        ok "inputs.yaml is valid YAML"
    else
        fail "inputs.yaml has YAML syntax errors"
    fi
else
    fail "inputs.yaml not found"
    exit 1
fi

if [[ ! -x "$LF_PYTHON" ]]; then
    echo "Cannot continue dryrun without swelf python. Fix the swelf environment first."
    exit 1
fi

if ! "$LF_PYTHON" -c "import yaml; yaml.safe_load(open('$CONFIG'))" 2>/dev/null; then
    echo "Cannot continue dryrun until inputs.yaml parses successfully."
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Conda environment
# ---------------------------------------------------------------------------
echo ""
echo "--- 2. Conda environment ---"
if [[ -d /anaconda3/envs/swelf ]]; then
    ok "conda env 'swelf' exists at /anaconda3/envs/swelf"
else
    fail "conda env 'swelf' not found — run: conda create -n swelf python=3.12 && pip install -e repos/LLaMA-Factory/ -e repos/swe_data_process/"
fi

# ---------------------------------------------------------------------------
# 3. Repos
# ---------------------------------------------------------------------------
echo ""
echo "--- 3. Repos ---"
if [[ -d "$LF_REPO" ]]; then
    ok "repos/LLaMA-Factory/ exists"
else
    fail "repos/LLaMA-Factory/ not found"
fi
if [[ -d "$SWE_DP_SRC" ]]; then
    ok "repos/swe_data_process/src/swe_data_process/ exists"
else
    fail "repos/swe_data_process/src/swe_data_process/ not found"
fi

# ---------------------------------------------------------------------------
# 4. Source data
# ---------------------------------------------------------------------------
echo ""
echo "--- 4. Source data ---"
PROVIDER="$(cfg "['source']['provider']")"
SCAFFOLD="$(cfg "['source']['scaffold']")"
JOB_DIR="$(cfg "['source']['job_dir']")"
SOURCE_DIR="$(cfg "['source']['source_dir']")"

info "provider=$PROVIDER  scaffold=$SCAFFOLD"

VALID_COMBINATIONS=(
    "jierun+openhands-sdk" "jierun+claude-code" "jierun+open-code" "jierun+terminus2"
    "chaofan+openhands" "chaofan+claude-code" "chaofan+open-code" "chaofan+terminus2" "chaofan+openhands-sdk"
)
COMBO="${PROVIDER}+${SCAFFOLD}"
COMBO_VALID=false
for c in "${VALID_COMBINATIONS[@]}"; do [[ "$c" == "$COMBO" ]] && COMBO_VALID=true && break; done

if $COMBO_VALID; then
    ok "provider+scaffold combination is valid: $COMBO"
else
    fail "Unknown provider+scaffold: '$COMBO'. Valid: ${VALID_COMBINATIONS[*]}"
fi

if [[ "$PROVIDER" == "jierun" ]]; then
    if [[ -z "$JOB_DIR" ]]; then
        fail "source.job_dir is empty — set it to the harbor job directory"
    elif [[ -d "$JOB_DIR" ]]; then
        ok "source.job_dir exists: $JOB_DIR"
    else
        warn "source.job_dir not found (may be on another node): $JOB_DIR"
    fi
else
    if [[ -z "$SOURCE_DIR" ]]; then
        fail "source.source_dir is empty — set it to the chaofan completions directory"
    elif [[ -d "$SOURCE_DIR" ]]; then
        ok "source.source_dir exists: $SOURCE_DIR"
    else
        warn "source.source_dir not found (may be on another node): $SOURCE_DIR"
    fi
fi

# ---------------------------------------------------------------------------
# 5. Converter script
# ---------------------------------------------------------------------------
echo ""
echo "--- 5. Converter script ---"
case "$COMBO" in
    jierun+openhands-sdk)  CONVERTER="openhands/convert_openhands_sdk_jierun_to_im.py" ;;
    jierun+claude-code)    CONVERTER="claudecode_opencode/convert_cc_jierun_to_im.py" ;;
    jierun+open-code)      CONVERTER="claudecode_opencode/convert_oc_jierun_to_im.py" ;;
    jierun+terminus2)      CONVERTER="terminus2/convert_terminus2_jierun_to_im.py" ;;
    chaofan+openhands)     CONVERTER="openhands/convert_openhands_chaofan_to_im.py" ;;
    chaofan+claude-code)   CONVERTER="claudecode_opencode/convert_cc_chaofan_to_im.py" ;;
    chaofan+open-code)     CONVERTER="claudecode_opencode/convert_oc_chaofan_to_im.py" ;;
    chaofan+terminus2)     CONVERTER="terminus2/convert_terminus2_chaofan_to_im.py" ;;
    chaofan+openhands-sdk) CONVERTER="openhands/convert_openhands_sdk_chaofan_to_im.py" ;;
    *)                     CONVERTER="" ;;
esac

if [[ -n "$CONVERTER" ]]; then
    if [[ -f "$SWE_DP_SRC/$CONVERTER" ]]; then
        ok "Converter exists: $CONVERTER"
    else
        fail "Converter not found: $SWE_DP_SRC/$CONVERTER"
    fi
fi

# ---------------------------------------------------------------------------
# 6. Conversion output paths
# ---------------------------------------------------------------------------
echo ""
echo "--- 6. Conversion output paths ---"
DATA_NAME="$(cfg "['conversion']['data_name']")"

if [[ -z "$DATA_NAME" ]]; then
    fail "conversion.data_name is empty"
else
    ok "conversion.data_name = $DATA_NAME"
    IM_OUTPUT="$BLOCK_DIR/artifacts/data/im_data/${DATA_NAME}.jsonl"
    LF_OUTPUT="$BLOCK_DIR/artifacts/data/lf_data/${DATA_NAME}.json"
    info "im_output = $IM_OUTPUT"
    info "lf_output = $LF_OUTPUT"
    if [[ -f "$IM_OUTPUT" && -f "$LF_OUTPUT" ]]; then
        COUNT=$("$LF_PYTHON" -c "import json; print(len(json.load(open('$LF_OUTPUT'))))" 2>/dev/null || echo "?")
        info "IM + LF output both exist ($COUNT LF records) — STEP 0 will be skipped"
    fi
fi

EXCL_RAW="$(cfg "['conversion']['exclude_repos_file']")"
EXCL="$(abspath "$EXCL_RAW")"
if [[ -z "$EXCL_RAW" ]]; then
    warn "conversion.exclude_repos_file is empty — repo filtering disabled"
elif [[ -f "$EXCL" ]]; then
    COUNT=$(wc -l < "$EXCL")
    ok "exclude_repos_file exists ($COUNT entries): $EXCL"
else
    fail "exclude_repos_file not found: $EXCL"
fi

# ---------------------------------------------------------------------------
# 7. Dataset name
# ---------------------------------------------------------------------------
echo ""
echo "--- 7. Dataset registration ---"
DATASET_NAME_RAW="$(cfg "['dataset']['name']")"
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
        DATASET_STATUS=$("$LF_PYTHON" - "$DATASET_INFO" "$DATASET_NAME" "$LF_FILENAME" <<'PYEOF'
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
MODEL_PATH="$(cfg "['model']['model_name_or_path']")"
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
OUTPUT_DIR="$(cfg "['training']['output_dir']")"
TEMPLATE="$(cfg "['training']['template']")"
EPOCHS="$(cfg "['training']['num_train_epochs']")"
LR="$(cfg "['training']['learning_rate']")"
info "template=$TEMPLATE  epochs=$EPOCHS  lr=$LR"
if [[ -z "$OUTPUT_DIR" ]]; then
    fail "training.output_dir is empty — set it (e.g. qwen3_8b_jierun_oh_sdk_1k_gbs64pbs1acc8_lr1e-4_epo4_think)"
else
    ok "training.output_dir = $OUTPUT_DIR"
    ABS_OUTPUT_DIR="$BLOCK_DIR/artifacts/model/$(basename "$OUTPUT_DIR")"
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
WANDB_MODE="$(cfg "['experiment']['wandb_mode']")"
WANDB_KEY="$(cfg "['credentials']['wandb_api_key']")"
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
        if [[ -n "$WANDB_KEY" ]]; then
            info "credentials.wandb_api_key is set (optional in offline mode)"
        else
            info "credentials.wandb_api_key is empty (allowed in offline mode)"
        fi
        ;;
    disabled)
        ok "experiment.wandb_mode=disabled"
        info "WandB logging will be disabled in the generated train YAML"
        ;;
    *)
        fail "experiment.wandb_mode must be one of: online, offline, disabled"
        ;;
esac

RUN_NAME_RAW="$(cfg "['experiment']['run_name']")"
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
N_GPUS="$(cfg "['infrastructure']['n_gpus_per_node']")"
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
