#!/bin/bash
# 1-node RL training run.
# Reads all config from inputs.yaml — edit that file before running.
# Run from anywhere: bash scripts/train_1node.sh
set -euo pipefail

export NCCL_NVLS_ENABLE=0
export NCCL_DEBUG=WARN

# ---------------------------------------------------------------------------
# Resolve block root and repo path
# ---------------------------------------------------------------------------
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$BLOCK_DIR/repos/harbor-verl-train"
CONFIG="$BLOCK_DIR/inputs.yaml"

# Parse inputs.yaml with Python.
cfg() { python3 -c "import yaml,sys; c=yaml.safe_load(open('$CONFIG')); print(c$1)"; }
abspath() {
    local p="$1"
    if [[ "$p" = /* ]]; then echo "$p"; else echo "$BLOCK_DIR/$p"; fi
}

# ---------------------------------------------------------------------------
# Load config values
# ---------------------------------------------------------------------------
MODEL_PATH="$(abspath "$(cfg "['model']['model_path']")")"
SERVED_MODEL_NAME="$(cfg "['model']['served_model_name']")"
VLLM_MODEL_NAME="$(echo "$MODEL_PATH" | rev | cut -d'/' -f1-2 | rev)"

NODE0_IP="$(cfg "['infrastructure']['node0_ip']")"
if [[ -z "$NODE0_IP" ]]; then
    NODE0_IP="$(hostname -I | awk '{print $1}')"
    echo "    Auto-detected node0_ip: $NODE0_IP  (override in inputs/index.yaml if wrong)"
fi
if [[ -z "$NODE0_IP" ]]; then
    echo "ERROR: could not determine node0_ip. Set it explicitly in inputs/index.yaml." >&2
    exit 1
fi
NNODES="$(cfg "['infrastructure']['n_nodes']")"
N_GPUS="$(cfg "['infrastructure']['n_gpus_per_node']")"
VLLM_TP_SIZE="$(cfg "['infrastructure']['vllm_tp_size']")"
VLLM_PORT="$(cfg "['infrastructure']['vllm_port']")"
K8S_KUBECONFIG="$(abspath "$(cfg "['infrastructure']['k8s_kubeconfig']")")"
K8S_NAMESPACE="$(cfg "['infrastructure']['k8s_namespace']")"
K8S_POD_STARTUP_TIMEOUT="$(cfg "['infrastructure']['k8s_pod_startup_timeout']")"

TRAIN_BATCH_SIZE="$(cfg "['training']['train_batch_size']")"
N_PARALLEL_TASKS="$(cfg "['training']['n_parallel_tasks']")"
MAX_TURNS="$(cfg "['training']['max_turns']")"
MAX_PROMPT_LENGTH="$(cfg "['training']['max_prompt_length']")"
MAX_RESPONSE_LENGTH="$(cfg "['training']['max_response_length']")"
VLLM_MAX_MODEL_LEN="$(cfg "['training']['vllm_max_model_len']")"

SWE_PARQUET="$(abspath "$(cfg "['data']['swe_parquet']")")"
VAL_PARQUET="$(abspath "$(cfg "['data']['val_parquet']")")"
TASKS_DIR="$(abspath "$(cfg "['data']['tasks_dir']")")"
VAL_TASKS_DIR="$(abspath "$(cfg "['data']['val_tasks_dir']")")"
DATA_DIR="$(abspath "$(cfg "['data']['data_dir']")")"

PROJECT_NAME="$(cfg "['experiment']['project_name']")"
EXP_NAME="$(cfg "['experiment']['exp_name']")-$(date +%Y%m%d)"
TRIALS_DIR="$(abspath "$(cfg "['experiment']['trials_base_dir']")")-$(date +%Y%m%d)"
WANDB_MODE="online"
WANDB_RUN_ID=""

ANTHROPIC_API_KEY="sk-dummy"
WANDB_API_KEY_VAL="$(cfg "['credentials']['wandb_api_key']")"

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
# Conda env bin (override via CONDA_ENV_BIN if your conda lives elsewhere; see CLAUDE.md)
export PATH="${CONDA_ENV_BIN:-/anaconda3/envs/harbor-rllm-env/bin}:$PATH"
export PYTHONPATH="$REPO/verl:$REPO"
export WANDB_API_KEY="$WANDB_API_KEY_VAL"
export WANDB_DISABLE_SERVICE=true
export WANDB_START_METHOD=thread
export WANDB_MODE="$WANDB_MODE"
[[ -n "$WANDB_RUN_ID" ]] && export WANDB_RESUME=allow && export WANDB_RUN_ID="$WANDB_RUN_ID"
export HARBOR_BACKEND=kubernetes
export KUBECONFIG="$K8S_KUBECONFIG"
export VLLM_ATTENTION_BACKEND=FLASH_ATTN
export TOKENIZERS_PARALLELISM=false
export VLLM_USE_V1=1
export VLLM_ALLOW_LONG_MAX_MODEL_LEN=1
export VLLM_ENGINE_ITERATION_TIMEOUT_S=10000000000
export OMP_NUM_THREADS=1
export RLLM_VLLM_HTTP_PORT=$VLLM_PORT

cd "$REPO"

# ===========================================================================
# STEP 0: Prepare Harbor task directories (idempotent)
# ===========================================================================
if [[ ! -d "$TASKS_DIR" ]] || [[ -z "$(ls -A "$TASKS_DIR" 2>/dev/null)" ]]; then
    echo "=== Preparing train Harbor task directories ==="
    python3 examples/harbor/prepare_swe_harbor_tasks.py \
        --parquet    "$SWE_PARQUET" \
        --output_dir "$TASKS_DIR"
else
    echo "=== Train task dirs already exist — skipping ==="
fi

if [[ ! -d "$VAL_TASKS_DIR" ]] || [[ -z "$(ls -A "$VAL_TASKS_DIR" 2>/dev/null)" ]]; then
    echo "=== Preparing val Harbor task directories ==="
    python3 examples/harbor/prepare_swe_harbor_tasks.py \
        --parquet    "$VAL_PARQUET" \
        --output_dir "$VAL_TASKS_DIR"
else
    echo "=== Val task dirs already exist — skipping ==="
fi

# ===========================================================================
# STEP 1: Build task index parquets (idempotent)
# ===========================================================================
TRAIN_INDEX="$DATA_DIR/train.parquet"
VAL_INDEX="$DATA_DIR/val.parquet"

if [[ ! -f "$TRAIN_INDEX" ]]; then
    echo "=== Building train task index ==="
    mkdir -p "$DATA_DIR"
    python3 examples/harbor/create_task_index.py \
        --tasks_dir "$TASKS_DIR" --output "$TRAIN_INDEX" --split all
else
    echo "=== Train index already exists — skipping ==="
fi

if [[ ! -f "$VAL_INDEX" ]]; then
    echo "=== Building val task index ==="
    mkdir -p "$DATA_DIR"
    python3 examples/harbor/create_task_index.py \
        --tasks_dir "$VAL_TASKS_DIR" --output "$VAL_INDEX" --split all
else
    echo "=== Val index already exists — skipping ==="
fi

# ===========================================================================
# STEP 2: Start LiteLLM proxy
# ===========================================================================
VLLM_DP_SIZE=$((N_GPUS * NNODES / VLLM_TP_SIZE))
LITELLM_PORT=$((VLLM_PORT + VLLM_DP_SIZE))
echo "    VLLM_DP_SIZE=$VLLM_DP_SIZE  vLLM ports=${VLLM_PORT}..$(( VLLM_PORT + VLLM_DP_SIZE - 1 ))  LITELLM_PORT=$LITELLM_PORT"

LITELLM_MODEL_ENTRIES=""
for ((dp_rank=0; dp_rank<VLLM_DP_SIZE; dp_rank++)); do
    PORT=$((VLLM_PORT + dp_rank))
    LITELLM_MODEL_ENTRIES+="
  - model_name: \"claude-*\"
    litellm_params:
      model: openai/${VLLM_MODEL_NAME}
      api_base: http://${NODE0_IP}:${PORT}/v1
      api_key: dummy
      use_responses_api: false
      stream: false
    model_info:
      supports_reasoning: false
  - model_name: \"hosted_vllm/${SERVED_MODEL_NAME}\"
    litellm_params:
      model: openai/${VLLM_MODEL_NAME}
      api_base: http://${NODE0_IP}:${PORT}/v1
      api_key: dummy
      use_responses_api: false
      stream: false
    model_info:
      supports_reasoning: false
  - model_name: \"${SERVED_MODEL_NAME}\"
    litellm_params:
      model: openai/${VLLM_MODEL_NAME}
      api_base: http://${NODE0_IP}:${PORT}/v1
      api_key: dummy
      use_responses_api: false
      stream: false
    model_info:
      supports_reasoning: false"
done

LITELLM_CONFIG="/tmp/litellm_cc_train_config_$LITELLM_PORT.yaml"
cat > "$LITELLM_CONFIG" << EOF
model_list:${LITELLM_MODEL_ENTRIES}

litellm_settings:
  drop_params: true
  request_timeout: 900
  use_chat_completions_url_for_anthropic_messages: true
  use_responses_api: false
EOF

LITELLM_LOG="/tmp/litellm_cc_train_$LITELLM_PORT.log"
if lsof -ti tcp:$LITELLM_PORT >/dev/null 2>&1; then
    echo "    Port $LITELLM_PORT in use — killing stale process(es)"
    lsof -ti tcp:$LITELLM_PORT | xargs kill -9 2>/dev/null || true
    sleep 1
fi

litellm --config "$LITELLM_CONFIG" --port $LITELLM_PORT --host 0.0.0.0 \
    >"$LITELLM_LOG" 2>&1 &
LITELLM_PID=$!
echo "    LiteLLM PID=$LITELLM_PID  log=$LITELLM_LOG"

echo "    Waiting for LiteLLM proxy..."
for i in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:$LITELLM_PORT/health" >/dev/null 2>&1; then
        echo "    LiteLLM proxy ready (${i}s)"; break
    fi
    if ! kill -0 $LITELLM_PID 2>/dev/null; then
        echo "ERROR: LiteLLM died. Log:"; tail -20 "$LITELLM_LOG"; exit 1
    fi
    sleep 1
    [[ $i -eq 60 ]] && echo "WARNING: LiteLLM health-check timed out (may still work)"
done

# ===========================================================================
# STEP 3: Launch training
# ===========================================================================
echo "=== Launching 1-node SWE-bench Harbor online RL training ==="

TRAIN_LOG="$REPO/logs/harbor_swe_cc_1node_$(date +%Y-%m-%d_%H-%M-%S).log"
mkdir -p "$(dirname "$TRAIN_LOG")"
echo "Log file: $TRAIN_LOG"

python3 examples/harbor/train_harbor_online_pipeline.py \
    --config-path  "$(realpath examples/harbor/config)" \
    --config-name  harbor_online_cc \
    algorithm.adv_estimator=grpo \
    actor_rollout_ref.model.path="$MODEL_PATH" \
    critic.model.path="$MODEL_PATH" \
    actor_rollout_ref.rollout.http_endpoint_port=$VLLM_PORT \
    actor_rollout_ref.rollout.max_model_len=$VLLM_MAX_MODEL_LEN \
    actor_rollout_ref.rollout.gpu_memory_utilization=0.85 \
    actor_rollout_ref.rollout.tensor_model_parallel_size=$VLLM_TP_SIZE \
    actor_rollout_ref.rollout.enforce_eager=False \
    actor_rollout_ref.rollout.dtype=bfloat16 \
    actor_rollout_ref.rollout.temperature=1.0 \
    actor_rollout_ref.rollout.n=8 \
    actor_rollout_ref.rollout.val_kwargs.n=1 \
    actor_rollout_ref.rollout.val_kwargs.temperature=0 \
    actor_rollout_ref.rollout.log_prob_use_dynamic_bsz=True \
    actor_rollout_ref.ref.log_prob_use_dynamic_bsz=True \
    +actor_rollout_ref.rollout.enable_http_tool_calling=True \
    +actor_rollout_ref.rollout.http_tool_parser=hermes \
    actor_rollout_ref.model.use_remove_padding=True \
    actor_rollout_ref.model.enable_gradient_checkpointing=True \
    critic.model.use_remove_padding=True \
    actor_rollout_ref.actor.optim.lr=1e-6 \
    actor_rollout_ref.actor.loss_agg_mode=seq-mean-token-mean \
    actor_rollout_ref.actor.policy_loss.loss_mode=gspo \
    actor_rollout_ref.actor.ppo_mini_batch_size=$TRAIN_BATCH_SIZE \
    actor_rollout_ref.actor.ppo_micro_batch_size_per_gpu=1 \
    actor_rollout_ref.actor.ulysses_sequence_parallel_size=4 \
    actor_rollout_ref.actor.ppo_max_token_len_per_gpu=25000 \
    critic.ulysses_sequence_parallel_size=4 \
    critic.ppo_max_token_len_per_gpu=25000 \
    actor_rollout_ref.actor.use_dynamic_bsz=True \
    actor_rollout_ref.actor.fsdp_config.param_offload=True \
    actor_rollout_ref.actor.fsdp_config.optimizer_offload=True \
    actor_rollout_ref.ref.fsdp_config.param_offload=True \
    actor_rollout_ref.actor.use_kl_loss=False \
    actor_rollout_ref.actor.clip_ratio_low=3e-4 \
    actor_rollout_ref.actor.clip_ratio_high=4e-4 \
    actor_rollout_ref.actor.kl_loss_coef=0.001 \
    actor_rollout_ref.actor.kl_loss_type=low_var_kl \
    actor_rollout_ref.actor.entropy_coeff=0.0 \
    data.train_files="$TRAIN_INDEX" \
    data.val_files="$VAL_INDEX" \
    data.train_batch_size=$TRAIN_BATCH_SIZE \
    data.max_prompt_length=$MAX_PROMPT_LENGTH \
    data.max_response_length=$MAX_RESPONSE_LENGTH \
    rllm.workflow.workflow_args.served_model_name="$SERVED_MODEL_NAME" \
    rllm.workflow.workflow_args.vllm_host="$NODE0_IP" \
    rllm.workflow.workflow_args.vllm_port=$VLLM_PORT \
    rllm.workflow.workflow_args.anthropic_proxy_host="$NODE0_IP" \
    rllm.workflow.workflow_args.anthropic_proxy_port=$LITELLM_PORT \
    rllm.workflow.workflow_args.anthropic_api_key="$ANTHROPIC_API_KEY" \
    "++rllm.workflow.workflow_args.harbor_cfg.trials_dir=$TRIALS_DIR" \
    "++rllm.workflow.workflow_args.harbor_cfg.agent.name=claude-code" \
    "++rllm.workflow.workflow_args.harbor_cfg.agent.kwargs.version=2.1.62" \
    "++rllm.workflow.workflow_args.harbor_cfg.agent.override_timeout_sec=1200" \
    "++rllm.workflow.workflow_args.harbor_cfg.agent.kwargs.max_turns=$MAX_TURNS" \
    "++rllm.workflow.workflow_args.harbor_cfg.agent.override_setup_timeout_sec=900" \
    "++rllm.workflow.workflow_args.harbor_cfg.agent_timeout_multiplier=2.0" \
    "++rllm.workflow.workflow_args.harbor_cfg.environment.override_cpus=1" \
    "++rllm.workflow.workflow_args.harbor_cfg.environment.override_memory_mb=1536" \
    "++rllm.workflow.workflow_args.harbor_cfg.environment.type=$HARBOR_BACKEND" \
    "++rllm.workflow.workflow_args.harbor_cfg.environment.kwargs.kubeconfig_path=$K8S_KUBECONFIG" \
    "++rllm.workflow.workflow_args.harbor_cfg.environment.kwargs.namespace=$K8S_NAMESPACE" \
    "++rllm.workflow.workflow_args.harbor_cfg.environment.kwargs.pod_startup_timeout_sec=$K8S_POD_STARTUP_TIMEOUT" \
    rllm.workflow.n_parallel_tasks=$N_PARALLEL_TASKS \
    rllm.rejection_sample.enable=True \
    rllm.rejection_sample.multiplier=1 \
    trainer.project_name="$PROJECT_NAME" \
    trainer.experiment_name="$EXP_NAME" \
    trainer.val_before_train=True \
    trainer.n_gpus_per_node=$N_GPUS \
    trainer.nnodes=$NNODES \
    trainer.total_epochs=3 \
    trainer.logger="['console','wandb']" \
    trainer.save_freq=5 \
    trainer.test_freq=5 \
    trainer.default_hdfs_dir=null \
    2>&1 | tee -a "$TRAIN_LOG"
