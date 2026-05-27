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
check "sync_1node_cc.sh" "$REPO/scripts/sync_1node_cc.sh"
check "verl_patch config" "$REPO/src/verl_patch/config/harbor_verl_sync.yaml"
check "agent_loop_config_cc" "$REPO/src/verl_patch/config/agent_loop_config_cc.yaml"

echo "[rl/dryrun] Python venv..."
VENV_FROM_CFG="$(abspath "$(cfg runtime_info.input.environment.venv_path)")"
if [[ -n "$VENV_FROM_CFG" ]]; then
    VENV_PATH="$VENV_FROM_CFG"
    VENV_MODE="custom"
else
    VENV_PATH="$REPO/.venv"
    VENV_MODE="default"
fi
check "venv/bin/python ($VENV_MODE)" "$VENV_PATH/bin/python"

echo "[rl/dryrun] Input paths from config.yaml..."
check "model_path"            "$(abspath "$(cfg runtime_info.input.model.model_path)")"
check "train_index"           "$(abspath "$(cfg runtime_info.input.data.train_index)")"
check "val_index"             "$(abspath "$(cfg runtime_info.input.data.val_index)")"
check "trajectory_logger_src" "$(abspath "$(cfg runtime_info.input.experiment.trajectory_logger_src)")"

ENV_IMPORT="$(cfg runtime_info.input.harbor_agent.environment_import_path)"
DOCKER_HOST_CFG="$(cfg runtime_info.input.harbor_agent.docker_host)"

if [[ "$ENV_IMPORT" == *"docker"* ]]; then
    echo "[rl/dryrun] Environment: Docker mode"
    if [[ "$DOCKER_HOST_CFG" == tcp://* ]]; then
        echo "  REMOTE   docker_host=$DOCKER_HOST_CFG"
        DOCKER_IP=$(echo "$DOCKER_HOST_CFG" | sed -E 's|^tcp://||; s|:.*||')
        DOCKER_PORT=$(echo "$DOCKER_HOST_CFG" | sed -E 's|.*:||')
        if [[ "$DOCKER_PORT" == "2375" ]]; then
            echo "  WARN     port 2375 is unencrypted (root-equivalent). Consider TLS on :2376."
        fi
        if timeout 2 bash -c "echo >/dev/tcp/$DOCKER_IP/$DOCKER_PORT" 2>/dev/null; then
            echo "  OK       remote Docker daemon reachable"; ok=$((ok+1))
        else
            echo "  MISSING  cannot reach $DOCKER_HOST_CFG (firewall or daemon not running)"; missing=$((missing+1))
        fi
    elif [[ "$DOCKER_HOST_CFG" == unix://* ]]; then
        SOCK="${DOCKER_HOST_CFG#unix://}"
        echo "  LOCAL    docker_host=$DOCKER_HOST_CFG"
        if [[ -S "$SOCK" ]]; then
            echo "  OK       socket exists: $SOCK"; ok=$((ok+1))
        else
            echo "  MISSING  socket not found: $SOCK"; missing=$((missing+1))
        fi
    else
        # Empty or unrecognized → local Docker daemon via default socket
        echo "  LOCAL    docker_host is empty — Docker defaults to unix:///var/run/docker.sock"
        if command -v docker >/dev/null 2>&1; then
            if docker info >/dev/null 2>&1; then
                echo "  OK       docker daemon is running"; ok=$((ok+1))
            else
                echo "  MISSING  docker daemon not running (try: systemctl start docker)"; missing=$((missing+1))
            fi
        else
            echo "  MISSING  docker CLI not found"; missing=$((missing+1))
        fi
    fi
else
    echo "[rl/dryrun] Environment: K8s mode"
    check "k8s.kubeconfig"    "$(abspath "$(cfg runtime_info.input.k8s.kubeconfig)")"
fi

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
echo "================================================================"
echo "[rl/dryrun] Run Configuration Summary"
echo "================================================================"
echo "  Model:        $(cfg runtime_info.input.model.model_path)"
echo "  Served as:    $(cfg runtime_info.input.model.served_model_name)"
echo "  Train data:   $(cfg runtime_info.input.data.train_index)"
echo "  Val data:     $(cfg runtime_info.input.data.val_index)"
echo "  Backend:      $([[ "$ENV_IMPORT" == *docker* ]] && echo "Docker ($DOCKER_HOST_CFG)" || echo "K8s ($(cfg runtime_info.input.k8s.kubeconfig))")"
echo "  Parallelism:  $(cfg runtime_info.input.harbor_runtime.num_workers) workers"
echo "  Batch size:   $(cfg runtime_info.input.training.train_batch_size) prompts × $(cfg runtime_info.input.training.n_resp_per_prompt) responses = $(( $(cfg runtime_info.input.training.train_batch_size) * $(cfg runtime_info.input.training.n_resp_per_prompt) )) trials/step"
echo "  Context:      prompt=$(cfg runtime_info.input.training.max_prompt_length) + response=$(cfg runtime_info.input.training.max_response_length)"
echo "  vLLM:         TP=$(cfg runtime_info.input.vllm.gen_tp)  max_model_len=$(cfg runtime_info.input.vllm.max_model_length)  gpu_mem=$(cfg runtime_info.input.vllm.gpu_memory_utilization)"
echo "  Algorithm:    $(cfg runtime_info.input.algorithm.adv_estimator) / $(cfg runtime_info.input.algorithm.policy_loss_mode)  lr=$(cfg runtime_info.input.algorithm.learning_rate)"
echo "  Epochs:       $(cfg runtime_info.input.training.total_epochs)  save_freq=$(cfg runtime_info.input.training.save_freq)  test_freq=$(cfg runtime_info.input.training.test_freq)"
echo "  Experiment:   project=$(cfg runtime_info.input.experiment.project_name)  exp=$(cfg runtime_info.input.experiment.exp_name || echo '<auto>')"
echo "  wandb:        $(cfg runtime_info.input.credentials.wandb_mode)"
echo "  Agent:        $(cfg runtime_info.input.harbor_agent.agent_name)  timeout=$(cfg runtime_info.input.harbor_agent.max_timeout_sec)s  retries=$(cfg runtime_info.input.harbor_agent.max_retries)"
echo "================================================================"
echo
echo "[rl/dryrun] Done. ok=$ok missing/empty=$missing"
[[ $missing -eq 0 ]] || exit 1
