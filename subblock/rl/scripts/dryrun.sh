#!/bin/bash
# Health-check: verify configs, paths, and GPU availability before launching training.
set -e

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$BLOCK_DIR/repos/harbor-verl-train"
CONFIG="$BLOCK_DIR/config.yaml"

cfg() {
    python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
v = yaml.safe_load(open(sys.argv[1]))
for k in sys.argv[2].split("."):
    v = v[k] if v is not None and k in v else None
    if v is None: break
print(v if v is not None else "")
PY
}
abspath() {
    local p="$1"
    if [[ -z "$p" ]]; then echo ""; elif [[ "$p" = /* ]]; then echo "$p"; else echo "$BLOCK_DIR/$p"; fi
}

ok=0; missing=0
check() {
    local label="$1" path="$2"
    if [[ -z "$path" ]]; then
        echo "  EMPTY    $label"; missing=$((missing+1))
    elif [ -e "$path" ]; then
        echo "  OK       $label  ($path)"; ok=$((ok+1))
    else
        echo "  MISSING  $label  ($path)"; missing=$((missing+1))
    fi
}

echo "[rl/dryrun] config.yaml is parseable..."
python3 -c "import yaml; yaml.safe_load(open('$CONFIG'))" && echo "  OK       $CONFIG"

echo "[rl/dryrun] Submodule repos present..."
check "harbor-verl-train" "$REPO"
check "harbor"            "$BLOCK_DIR/repos/harbor"
check "verl"              "$BLOCK_DIR/repos/verl"

echo "[rl/dryrun] Upstream launch script..."
check "sync_1nodes_cc.sh" "$REPO/scripts/sync_1nodes_cc.sh"
check "verl_patch config" "$REPO/src/verl_patch/config/harbor_verl_sync.yaml"
check "agent_loop_config_cc" "$REPO/src/verl_patch/config/agent_loop_config_cc.yaml"

echo "[rl/dryrun] Python venv..."
check ".venv/bin/python" "$REPO/.venv/bin/python"

echo "[rl/dryrun] Input paths from config.yaml..."
check "model_path"            "$(abspath "$(cfg runtime_info.input.model.model_path)")"
check "train_index"           "$(abspath "$(cfg runtime_info.input.data.train_index)")"
check "val_index"             "$(abspath "$(cfg runtime_info.input.data.val_index)")"
check "k8s.kubeconfig"        "$(abspath "$(cfg runtime_info.input.k8s.kubeconfig)")"
check "trajectory_logger_src" "$(abspath "$(cfg runtime_info.input.experiment.trajectory_logger_src)")"

echo "[rl/dryrun] WANDB_API_KEY..."
WANDB_FROM_CFG="$(cfg runtime_info.input.credentials.wandb_api_key)"
WANDB_FROM_ENV="${WANDB_API_KEY:-}"
if [[ -n "$WANDB_FROM_CFG" || -n "$WANDB_FROM_ENV" ]]; then
    echo "  OK       wandb_api_key set ($([[ -n "$WANDB_FROM_CFG" ]] && echo config || echo env))"
else
    echo "  MISSING  WANDB_API_KEY — export it in your shell before launch (do NOT hardcode it in config.yaml). To opt out, set credentials.wandb_mode: disabled."
    missing=$((missing+1))
fi

echo "[rl/dryrun] GPU availability..."
if command -v nvidia-smi >/dev/null; then
    nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | sed 's/^/  /'
else
    echo "  nvidia-smi not available"
fi

KV_HEADS_FILE="$(abspath "$(cfg runtime_info.input.model.model_path)")/config.json"
GEN_TP="$(cfg runtime_info.input.vllm.gen_tp)"
if [[ -f "$KV_HEADS_FILE" && -n "$GEN_TP" ]]; then
    KV_HEADS=$(python3 -c "import json; print(json.load(open('$KV_HEADS_FILE'))['num_key_value_heads'])" 2>/dev/null || echo "")
    if [[ -n "$KV_HEADS" ]]; then
        if (( KV_HEADS % GEN_TP == 0 )); then
            echo "[rl/dryrun] vllm.gen_tp=$GEN_TP divides num_key_value_heads=$KV_HEADS — OK"
        else
            echo "[rl/dryrun] WARN: vllm.gen_tp=$GEN_TP does NOT divide num_key_value_heads=$KV_HEADS"
            echo "           This will crash with CUDA illegal memory access at first forward pass."
            missing=$((missing+1))
        fi
    fi
fi

echo
echo "[rl/dryrun] Done. ok=$ok missing/empty=$missing"
[[ $missing -eq 0 ]] || exit 1
