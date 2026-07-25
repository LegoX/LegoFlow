#!/bin/bash
# Validate config, paths, converter modules, and environment without running training.
# Run from anywhere: bash scripts/dryrun.sh
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LF_REPO="$BLOCK_DIR/repos/LLaMA-Factory"
SWE_DP_REPO="$BLOCK_DIR/repos/swe_data_process"
SWE_DP_SRC="$SWE_DP_REPO/src"
CONFIG="${SFT_CONFIG:-$BLOCK_DIR/config.yaml}"
CONFIG_PYTHON="${CONFIG_PYTHON:-python3}"

PASS=0
FAIL=0
WARN=0

ok()   { echo "  [OK]   $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $1"; WARN=$((WARN+1)); }

# --- shared block-contract validation (schema, deps, fill markers) ------------
REPO_ROOT="$(cd "$BLOCK_DIR/../.." && pwd)"
if [[ -f "$REPO_ROOT/scripts/validate_config.py" ]]; then
  if VAL_OUT="$(python3 "$REPO_ROOT/scripts/validate_config.py" --block "$BLOCK_DIR" 2>&1)"; then
    ok "validate_config: block contract OK"
  else
    echo "$VAL_OUT" | sed 's/^/    /'
    fail "validate_config reported failures (see lines above)"
  fi
else
  warn "shared validator not found at <repo_root>/scripts/validate_config.py — skipping contract validation"
fi
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

echo "=== trainer dryrun: $BLOCK_DIR ==="
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

if command -v uv &>/dev/null && [[ -x "$LF_PYTHON" ]]; then
    if UV_CHECK_OUTPUT="$(uv pip check --python "$LF_PYTHON" 2>&1)"; then
        ok "SFT uv package dependencies are compatible"
    else
        fail "SFT uv package dependency conflicts detected; rerun scripts/install_env.sh"
        while IFS= read -r line; do info "$line"; done <<< "$UV_CHECK_OUTPUT"
    fi
fi

# ---------------------------------------------------------------------------
# 3. Repos and imports
# ---------------------------------------------------------------------------
echo ""
echo "--- 3. Repos and Python modules ---"
check_repo_pin() {
    local label="$1" repo="$2" expected="$3" head
    if [[ -z "$expected" ]]; then
        fail "$label commit pin is empty"
        return
    fi
    head="$(git -C "$repo" rev-parse HEAD 2>/dev/null || true)"
    if [[ "$head" == "$expected" ]]; then
        ok "$label HEAD matches configured pin: $expected"
    else
        fail "$label HEAD=$head does not match configured pin=$expected"
    fi
}

if [[ -d "$LF_REPO" ]]; then
    ok "repos/LLaMA-Factory/ exists"
    check_repo_pin "LLaMA-Factory" "$LF_REPO" "$(meta_cfg "repositories.llama_factory.commit")"
else
    fail "repos/LLaMA-Factory/ not found"
fi

if [[ -f "$SWE_DP_REPO/pyproject.toml" && -d "$SWE_DP_SRC/swe_data_process" ]]; then
    ok "repos/swe_data_process is an installable src-layout package"
    check_repo_pin "swe_data_process" "$SWE_DP_REPO" "$(meta_cfg "repositories.swe_data_process.commit")"
else
    fail "repos/swe_data_process package files not found"
fi

if [[ -x "$LF_PYTHON" ]] && PYTHONPATH="$SWE_DP_SRC:${PYTHONPATH:-}" "$LF_PYTHON" -c "import swe_data_process" 2>/dev/null; then
    ok "swe_data_process is importable with local PYTHONPATH"
else
    warn "skipping or failing swe_data_process import check because SFT uv python is unavailable or import failed"
fi

if [[ -x "$LF_PYTHON" ]] && PYTHONPATH="$LF_REPO/src:$SWE_DP_SRC:${PYTHONPATH:-}" "$LF_PYTHON" -c "import llamafactory.hparams" 2>/dev/null; then
    ok "LLaMA-Factory imports and dependency checks pass"
else
    fail "LLaMA-Factory import/dependency check failed; rerun scripts/install_env.sh"
fi

# ---------------------------------------------------------------------------
# 4. Source data
# ---------------------------------------------------------------------------
echo ""
echo "--- 4. Source data ---"
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
CONVERTER_MODULE=""

case "$SOURCE_TYPE" in
    harbor_job)
        ok "source.type = harbor_job (convert raw Harbor trajectories)"
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
        ;;
    hf_lf)
        ok "source.type = hf_lf (load a ready-made LF dataset from the HuggingFace Hub)"
        if [[ -z "$HF_HUB_URL" ]]; then
            fail "source.hf_hub_url is empty — set runtime_info.input.source.hf_hub_url"
        else
            ok "source.hf_hub_url = $HF_HUB_URL"
            if [[ -n "$HF_FILE_NAME" ]]; then
                ok "source.hf_file_name = $HF_FILE_NAME (only this file will be downloaded)"
            fi
            [[ -n "$HF_SUBSET" ]] && info "hf_subset=$HF_SUBSET"
            info "hf_split=${HF_SPLIT:-train}"
        fi
        if [[ -n "${HF_TOKEN:-$(cfg "credentials.hf_token")}" ]]; then
            ok "Hugging Face token is available from the private runtime environment"
        else
            info "credentials.hf_token is empty — Hub access must be public or pre-authenticated on this host"
        fi
        ;;
    local_lf)
        ok "source.type = local_lf (use an existing local LF json)"
        if [[ -z "$LF_PATH" ]]; then
            fail "source.lf_path is empty — set runtime_info.input.source.lf_path"
        elif [[ -f "$LF_PATH" ]]; then
            ok "source.lf_path exists: $LF_PATH"
        else
            fail "source.lf_path not found: $LF_PATH"
        fi
        ;;
    *)
        fail "Unsupported source.type '$SOURCE_TYPE' — valid: harbor_job | hf_lf | local_lf"
        ;;
esac

# ---------------------------------------------------------------------------
# 5. Converter module
# ---------------------------------------------------------------------------
echo ""
echo "--- 5. Converter module ---"
if [[ "$SOURCE_TYPE" != "harbor_job" ]]; then
    info "source.type=$SOURCE_TYPE — no trajectory converter needed"
elif [[ -n "$CONVERTER_MODULE" ]]; then
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
# 6. Data acquisition / conversion outputs
# ---------------------------------------------------------------------------
echo ""
echo "--- 6. Data acquisition / conversion outputs ---"
DATA_NAME="$(cfg "conversion.data_name")"
MAX_INSTANCES_DRYRUN="$(cfg "conversion.max_instances")"
IM_OUTPUT="$BLOCK_DIR/artifacts/data/im_data/${DATA_NAME}.jsonl"
LF_OUTPUT="$BLOCK_DIR/artifacts/data/lf_data/${DATA_NAME}.json"

if [[ -z "$DATA_NAME" ]]; then
    fail "conversion.data_name is empty"
else
    ok "conversion.data_name = $DATA_NAME"
fi

if [[ "$SOURCE_TYPE" == "harbor_job" ]]; then
    info "im_output = $IM_OUTPUT"
    info "lf_output = $LF_OUTPUT"
    if [[ -f "$IM_OUTPUT" && -f "$LF_OUTPUT" ]]; then
        COUNT=$("$CONFIG_PYTHON" -c 'import json, sys; print(len(json.load(open(sys.argv[1], encoding="utf-8"))))' "$LF_OUTPUT" 2>/dev/null || echo "?")
        info "IM + LF output both exist ($COUNT LF records) — STEP 0 will be skipped"
    elif [[ -f "$IM_OUTPUT" || -f "$LF_OUTPUT" ]]; then
        fail "Partial conversion output exists; delete or restore the missing IM/LF pair before running conversion"
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
elif [[ "$SOURCE_TYPE" == "hf_lf" && -n "$HF_FILE_NAME" ]]; then
    HF_DOWNLOAD_TARGET="$BLOCK_DIR/artifacts/data/hf_data/${HF_HUB_URL//\//__}/$HF_FILE_NAME"
    info "exact Hub file target = $HF_DOWNLOAD_TARGET"
    if [[ -f "$HF_DOWNLOAD_TARGET" ]]; then
        ok "Exact Hub file is already cached locally"
    else
        info "Exact Hub file is not cached yet — STEP 0 will download it"
    fi
    if [[ -n "$MAX_INSTANCES_DRYRUN" ]] && [[ "$MAX_INSTANCES_DRYRUN" -gt 0 ]] 2>/dev/null; then
        info "conversion.max_instances=$MAX_INSTANCES_DRYRUN — applied as the dataset's num_samples (random subsample)"
    fi
else
    info "source.type=$SOURCE_TYPE — STEP 0 conversion skipped; data_name is used only as the dataset key"
    if [[ -n "$MAX_INSTANCES_DRYRUN" ]] && [[ "$MAX_INSTANCES_DRYRUN" -gt 0 ]] 2>/dev/null; then
        info "conversion.max_instances=$MAX_INSTANCES_DRYRUN — applied as the dataset's num_samples (random subsample)"
    fi
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
    case "$SOURCE_TYPE" in
        harbor_job) EXPECT_MODE="file";   EXPECT_VALUE="$(basename "$LF_OUTPUT")" ;;
        local_lf)   EXPECT_MODE="file";   EXPECT_VALUE="$LF_PATH" ;;
        hf_lf)
            if [[ -n "$HF_FILE_NAME" ]]; then
                EXPECT_MODE="file"
                EXPECT_VALUE="$BLOCK_DIR/artifacts/data/hf_data/${HF_HUB_URL//\//__}/$HF_FILE_NAME"
            else
                EXPECT_MODE="hf_hub"
                EXPECT_VALUE="$HF_HUB_URL"
            fi
            ;;
        *)          EXPECT_MODE="file";   EXPECT_VALUE="$(basename "$LF_OUTPUT")" ;;
    esac
    DATASET_INFO="$BLOCK_DIR/artifacts/data/lf_data/dataset_info.json"
    if [[ -f "$DATASET_INFO" ]]; then
        DATASET_STATUS=$("$CONFIG_PYTHON" - "$DATASET_INFO" "$DATASET_NAME" "$EXPECT_MODE" "$EXPECT_VALUE" <<'PYEOF'
import json
import sys

info_path, dataset_name, mode, value = sys.argv[1:5]

with open(info_path, encoding="utf-8") as fh:
    info = json.load(fh)

entry = info.get(dataset_name)
key = "hf_hub_url" if mode == "hf_hub" else "file_name"
if entry is None:
    print("missing")
elif entry.get(key) == value:
    print("matched")
else:
    print(f"mismatch:{entry.get('hf_hub_url') or entry.get('file_name')}")
PYEOF
)
        case "$DATASET_STATUS" in
            matched)
                info "Dataset '$DATASET_NAME' already points to $EXPECT_VALUE — STEP 1 will be skipped"
                ;;
            mismatch:*)
                warn "Dataset '$DATASET_NAME' currently points to ${DATASET_STATUS#mismatch:}; STEP 1 will update it to $EXPECT_VALUE"
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
elif [[ "$MODEL_PATH" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
    ok "Model is a Hugging Face Hub ID (availability is checked at train time)"
else
    fail "Model directory not found and value is not a Hub ID: $MODEL_PATH"
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
DEEPSPEED_RAW="$(cfg "training.deepspeed")"
DEEPSPEED_PATH="$(abspath "$DEEPSPEED_RAW")"
RESUME_FROM_CHECKPOINT="$(cfg "training.resume_from_checkpoint")"
OVERWRITE_OUTPUT_DIR="$(cfg "training.overwrite_output_dir")"
info "template=$TEMPLATE  epochs=$EPOCHS  lr=$LR"
if [[ -f "$DEEPSPEED_PATH" ]]; then
    ok "DeepSpeed config exists: $DEEPSPEED_PATH"
else
    fail "DeepSpeed config not found: $DEEPSPEED_PATH"
fi
if [[ -z "$OUTPUT_DIR" ]]; then
    fail "training.output_dir is empty — set a unique run name (e.g. qwen3_5_35b_dataset_gbs64_lr5e-5_epo3)"
else
    ok "training.output_dir = $OUTPUT_DIR"
    ABS_OUTPUT_DIR="$(resolve_output_dir "$OUTPUT_DIR")"
    TRAIN_YAML_NAME="$(basename "$OUTPUT_DIR").yaml"
    info "train YAML will be generated at: artifacts/training_config/$TRAIN_YAML_NAME"
    info "model checkpoints will be saved to: $ABS_OUTPUT_DIR"
    if [[ -f "$BLOCK_DIR/artifacts/training_config/$TRAIN_YAML_NAME" ]]; then
        info "YAML already exists from a previous run — will be overwritten"
    fi
    if [[ -d "$ABS_OUTPUT_DIR" && -n "$(find "$ABS_OUTPUT_DIR" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
        if [[ -n "$RESUME_FROM_CHECKPOINT" && "$RESUME_FROM_CHECKPOINT" != "null" ]]; then
            ok "non-empty output_dir will resume from checkpoint: $RESUME_FROM_CHECKPOINT"
        elif [[ "$OVERWRITE_OUTPUT_DIR" == "true" && "${SFT_ALLOW_OVERWRITE_OUTPUT:-0}" == "1" ]]; then
            warn "non-empty output_dir overwrite explicitly allowed by SFT_ALLOW_OVERWRITE_OUTPUT=1"
        else
            fail "output_dir is non-empty: $ABS_OUTPUT_DIR — choose a new run name; destructive overwrite requires overwrite_output_dir=true and SFT_ALLOW_OVERWRITE_OUTPUT=1"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 10. WandB key
# ---------------------------------------------------------------------------
echo ""
echo "--- 10. Credentials ---"
WANDB_MODE="$(cfg "experiment.wandb_mode")"
WANDB_KEY="${WANDB_API_KEY:-$(cfg "credentials.wandb_api_key")}"
case "$WANDB_MODE" in
    online)
        if [[ -n "$WANDB_KEY" ]]; then
            ok "WANDB_API_KEY is available for online WandB logging"
        else
            fail "WANDB_API_KEY is empty — required when experiment.wandb_mode=online"
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
# 12. VRAM headroom estimate (cutoff_len vs GPU memory)
# ---------------------------------------------------------------------------
echo ""
echo "--- 12. VRAM estimate ---"
CUTOFF_LEN="$(cfg "training.cutoff_len")"
PBS="$(cfg "training.per_device_train_batch_size")"
UNSLOTH_GC="$(cfg "training.use_unsloth_gc")"
FINETUNING_TYPE="$(cfg "training.finetuning_type")"
VRAM_MODEL_PATH="$(cfg "model.model_name_or_path")"
if ! command -v nvidia-smi &>/dev/null || ! [[ "$N_GPUS" =~ ^[0-9]+$ ]]; then
    info "nvidia-smi or valid n_gpus_per_node unavailable — skipping VRAM estimate"
elif [[ "$FINETUNING_TYPE" != "full" ]]; then
    info "VRAM estimate covers finetuning_type=full (ZeRO-3 + Adam) only — skipping for '$FINETUNING_TYPE'"
else
    GPU_MEM_MIB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | sort -n | head -1 | tr -d ' ')"
    VRAM_OUT="$(VRAM_MODEL_PATH="$VRAM_MODEL_PATH" CUTOFF_LEN="$CUTOFF_LEN" PBS="$PBS" \
        VRAM_N_GPUS="$N_GPUS" UNSLOTH_GC="$UNSLOTH_GC" GPU_MEM_MIB="$GPU_MEM_MIB" \
        "$CONFIG_PYTHON" - <<'PYEOF' 2>/dev/null || true
import glob, json, os, re, sys

model = os.environ["VRAM_MODEL_PATH"]
cutoff = int(float(os.environ.get("CUTOFF_LEN") or 0))
pbs = int(float(os.environ.get("PBS") or 1))
gpus = int(os.environ["VRAM_N_GPUS"])
unsloth = os.environ.get("UNSLOTH_GC", "").lower() == "true"
cap_gib = float(os.environ.get("GPU_MEM_MIB") or 0) / 1024

def find_model_dir(m):
    if os.path.isdir(m):
        return m
    hub = os.path.join(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")),
                       "hub", "models--" + m.replace("/", "--"), "snapshots")
    for s in sorted(glob.glob(hub + "/*"), key=os.path.getmtime, reverse=True):
        if os.path.isfile(os.path.join(s, "config.json")):
            return s
    return None

d = find_model_dir(model)
if not d:
    print("SKIP=model config not cached locally yet — estimate will appear once the model is downloaded")
    sys.exit(0)
cfg = json.load(open(os.path.join(d, "config.json")))
tc = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else {}
def g(k):
    return cfg.get(k, tc.get(k) if tc else None)
hidden, layers = g("hidden_size"), g("num_hidden_layers")
params = None
idx = os.path.join(d, "model.safetensors.index.json")
if os.path.isfile(idx):
    try:
        params = json.load(open(idx))["metadata"]["total_size"] / 2  # bf16 weights
    except Exception:
        pass
if params is None:
    sizes = re.findall(r"(\d+(?:\.\d+)?)\s*[Bb]", os.path.basename(model.rstrip("/")))
    if sizes:
        params = float(sizes[0]) * 1e9
if not (hidden and layers and params and cutoff and cap_gib):
    print("SKIP=could not determine model dims/params/cutoff/GPU capacity")
    sys.exit(0)

GIB = 2 ** 30
# ZeRO-3 + Adam, bf16: 2B param + 2B grad + 4B master + 4B momentum + 4B variance, sharded
states = 16 * params / gpus / GIB
# Activation slope per token: one layer's recompute + backward temporaries under
# gradient checkpointing with flash-attn/liger (calibrated for Qwen3.5-MoE-scale widths)
per_tok = 80 * hidden
if not unsloth:
    per_tok += 2 * layers * hidden  # plain HF GC keeps layer-boundary checkpoints on GPU
act = pbs * cutoff * per_tok / GIB
overhead = 15.0  # CUDA context + NCCL + DS gather/prefetch buffers + allocator fragmentation
total = states + act + overhead
budget = 0.9 * cap_gib
max_cutoff = int((budget - states - overhead) * GIB / (per_tok * pbs)) if budget > states + overhead else 0
print(f"EST={total:.0f}")
print(f"CAP={cap_gib:.0f}")
print(f"MAX_CUTOFF={max_cutoff}")
print(f"DETAIL=states≈{states:.0f} GiB (ZeRO-3/{gpus} GPU, Adam) + act≈{act:.0f} GiB "
      f"(cutoff={cutoff}, pbs={pbs}, unsloth_gc={str(unsloth).lower()}) + overhead≈{overhead:.0f} GiB")
PYEOF
)"
    if [[ -z "$VRAM_OUT" ]]; then
        info "VRAM estimate unavailable (estimator error) — skipping"
    elif grep -q '^SKIP=' <<<"$VRAM_OUT"; then
        info "VRAM estimate skipped: $(sed -n 's/^SKIP=//p' <<<"$VRAM_OUT")"
    else
        VRAM_EST="$(sed -n 's/^EST=//p' <<<"$VRAM_OUT")"
        VRAM_CAP="$(sed -n 's/^CAP=//p' <<<"$VRAM_OUT")"
        VRAM_MAX_CUTOFF="$(sed -n 's/^MAX_CUTOFF=//p' <<<"$VRAM_OUT")"
        info "$(sed -n 's/^DETAIL=//p' <<<"$VRAM_OUT")"
        info "rough estimate (±30%): assumes bf16 + ZeRO-3 + Adam + gradient checkpointing + flash-attn"
        VRAM_GRADE="$(awk -v e="$VRAM_EST" -v c="$VRAM_CAP" 'BEGIN{print (e<=0.85*c)?"ok":(e<=c)?"tight":"over"}')"
        case "$VRAM_GRADE" in
            ok)    ok "estimated peak ≈ ${VRAM_EST} GiB/GPU fits ${VRAM_CAP} GiB (≤85%); cutoff_len could go up to ≈${VRAM_MAX_CUTOFF} tokens" ;;
            tight) warn "estimated peak ≈ ${VRAM_EST} GiB/GPU is >85% of ${VRAM_CAP} GiB — cutoff_len=${CUTOFF_LEN} is tight; consider ≤${VRAM_MAX_CUTOFF} or pbs=1" ;;
            over)  warn "estimated peak ≈ ${VRAM_EST} GiB/GPU exceeds ${VRAM_CAP} GiB — likely OOM at cutoff_len=${CUTOFF_LEN}; reduce toward ≈${VRAM_MAX_CUTOFF} (or lower pbs / enable use_unsloth_gc)" ;;
        esac
    fi
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
