#!/bin/bash
# ============================================================================
# Block-level launcher: 1-node sync-mode online RL with claude-code agent.
#
# This is a thin wrapper around the upstream launch script:
#   repos/harbor-verl-train/scripts/sync_1nodes_cc.sh
#
# Responsibility split:
#   - This file:    parse config.yaml → export env vars → exec upstream
#   - Upstream:     all training/launch logic (vLLM/Ray, LiteLLM, verl PPO)
#
# Run from anywhere:  bash scripts/train_1node_cc.sh
# ============================================================================
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$BLOCK_DIR/repos/harbor-verl-train"
UPSTREAM="$REPO/scripts/sync_1nodes_cc.sh"
CONFIG="$BLOCK_DIR/config.yaml"

[[ -f "$UPSTREAM" ]] || { echo "ERROR: upstream script missing: $UPSTREAM" >&2; exit 1; }
[[ -f "$CONFIG"   ]] || { echo "ERROR: config.yaml missing: $CONFIG" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Read config.yaml → env vars
# ---------------------------------------------------------------------------
# yq if available, else python3+yaml. Python is more portable — use it.
cfg() {
    python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
path, dotted = sys.argv[1], sys.argv[2]
v = yaml.safe_load(open(path))
for k in dotted.split("."):
    v = v[k] if v is not None and k in v else None
    if v is None:
        break
if v is None or v == "":
    print("")
elif isinstance(v, bool):
    print("True" if v else "False")
else:
    print(v)
PY
}

abspath() {
    local p="$1"
    if [[ -z "$p" ]]; then echo ""; elif [[ "$p" = /* ]]; then echo "$p"; else echo "$BLOCK_DIR/$p"; fi
}

# Export only if value is non-empty (lets upstream defaults apply otherwise).
export_if_set() {
    local var="$1" val="$2"
    if [[ -n "$val" ]]; then export "$var=$val"; fi
}

# ---------------------------------------------------------------------------
# runtime_info.input.* → env vars consumed by sync_1nodes_cc.sh
# ---------------------------------------------------------------------------

# Python environment — user-overridable. Empty → upstream defaults to
# $REPO/.venv (built by setup_env.sh on first run).
export_if_set VENV_PATH "$(abspath "$(cfg runtime_info.input.environment.venv_path)")"

# Model
export_if_set MODEL_PATH        "$(cfg runtime_info.input.model.model_path)"
export_if_set SERVED_MODEL_NAME "$(cfg runtime_info.input.model.served_model_name)"

# Data
export_if_set DATA_DIR    "$(cfg runtime_info.input.data.data_dir)"
export_if_set TRAIN_INDEX "$(cfg runtime_info.input.data.train_index)"
export_if_set VAL_INDEX   "$(cfg runtime_info.input.data.val_index)"

# Infrastructure
export_if_set NODE0_IP            "$(cfg runtime_info.input.infrastructure.node0_ip)"
export_if_set NNODES              "$(cfg runtime_info.input.infrastructure.nnodes)"
export_if_set NGPUS_PER_NODE      "$(cfg runtime_info.input.infrastructure.ngpus_per_node)"
export_if_set RAY_PORT            "$(cfg runtime_info.input.infrastructure.ray_port)"
export_if_set RAY_DASHBOARD_PORT  "$(cfg runtime_info.input.infrastructure.ray_dashboard_port)"
export_if_set LITELLM_PORT        "$(cfg runtime_info.input.infrastructure.litellm_port)"
export_if_set VLLM_LOGGING_LEVEL  "$(cfg runtime_info.input.infrastructure.vllm_logging_level)"
export_if_set ANTHROPIC_API_KEY   "$(cfg runtime_info.input.infrastructure.anthropic_api_key)"

# K8s
export_if_set K8S_KUBECONFIG                "$(abspath "$(cfg runtime_info.input.k8s.kubeconfig)")"
export_if_set K8S_NAMESPACE                 "$(cfg runtime_info.input.k8s.namespace)"
export_if_set K8S_POD_STARTUP_TIMEOUT       "$(cfg runtime_info.input.k8s.pod_startup_timeout)"
export_if_set K8S_POD_ACTIVE_DEADLINE_SECONDS "$(cfg runtime_info.input.k8s.pod_active_deadline_seconds)"

# Harbor agent loop
export_if_set HARBOR_AGENT_NAME                       "$(cfg runtime_info.input.harbor_agent.agent_name)"
export_if_set HARBOR_AGENT_IMPORT_PATH                "$(cfg runtime_info.input.harbor_agent.agent_import_path)"
export_if_set HARBOR_AGENT_RUNTIME_IMAGE              "$(cfg runtime_info.input.harbor_agent.runtime_image)"
export_if_set HARBOR_AGENT_RUNTIME_MOUNT_PATH         "$(cfg runtime_info.input.harbor_agent.runtime_mount_path)"
export_if_set HARBOR_AGENT_RUNTIME_IMAGE_SUBPATH      "$(cfg runtime_info.input.harbor_agent.runtime_image_subpath)"
export_if_set HARBOR_AGENT_MAX_ITERATIONS             "$(cfg runtime_info.input.harbor_agent.max_iterations)"
export_if_set HARBOR_AGENT_MAX_TIMEOUT_SEC            "$(cfg runtime_info.input.harbor_agent.max_timeout_sec)"
export_if_set HARBOR_AGENT_OVERRIDE_TIMEOUT_SEC       "$(cfg runtime_info.input.harbor_agent.override_timeout_sec)"
export_if_set HARBOR_AGENT_DISABLE_TOOL_CALLS         "$(cfg runtime_info.input.harbor_agent.disable_tool_calls)"
export_if_set HARBOR_AGENT_TEMPERATURE                "$(cfg runtime_info.input.harbor_agent.temperature)"
export_if_set HARBOR_MAX_RETRIES                      "$(cfg runtime_info.input.harbor_agent.max_retries)"
export_if_set HARBOR_VERIFIER_ENABLED                 "$(cfg runtime_info.input.harbor_agent.verifier_enabled)"
export_if_set HARBOR_POD_NAME_PREFIX                  "$(cfg runtime_info.input.harbor_agent.pod_name_prefix)"
export_if_set HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER   "$(cfg runtime_info.input.harbor_agent.agent_setup_timeout_multiplier)"
export_if_set HARBOR_AGENT_TIMEOUT_MULTIPLIER         "$(cfg runtime_info.input.harbor_agent.agent_timeout_multiplier)"
export_if_set HARBOR_VERIFIER_TIMEOUT_MULTIPLIER      "$(cfg runtime_info.input.harbor_agent.verifier_timeout_multiplier)"
export_if_set HARBOR_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER "$(cfg runtime_info.input.harbor_agent.environment_build_timeout_multiplier)"
export_if_set HARBOR_ENVIRONMENT_TYPE                 "$(cfg runtime_info.input.harbor_agent.environment_type)"
export_if_set HARBOR_ENVIRONMENT_IMPORT_PATH          "$(cfg runtime_info.input.harbor_agent.environment_import_path)"
export_if_set HARBOR_ENVIRONMENT_FORCE_BUILD          "$(cfg runtime_info.input.harbor_agent.environment_force_build)"
export_if_set HARBOR_ENVIRONMENT_DELETE               "$(cfg runtime_info.input.harbor_agent.environment_delete)"
export_if_set HARBOR_ENVIRONMENT_OVERRIDE_CPUS        "$(cfg runtime_info.input.harbor_agent.environment_override_cpus)"

# Harbor runtime / tail-killer
export_if_set NUM_WORKERS                "$(cfg runtime_info.input.harbor_runtime.num_workers)"
# upstream uses lowercase env var name `test_freq`
export_if_set test_freq                  "$(cfg runtime_info.input.training.test_freq)"
export_if_set HARBOR_TAIL_KILL_TARGET    "$(cfg runtime_info.input.harbor_runtime.tail_kill_target)"
export_if_set HARBOR_TAIL_KILL_GRACE_SEC "$(cfg runtime_info.input.harbor_runtime.tail_kill_grace_sec)"
export_if_set HARBOR_TAIL_KILL_MIN_TASKS "$(cfg runtime_info.input.harbor_runtime.tail_kill_min_tasks)"

# Experiment
export_if_set PROJECT_NAME           "$(cfg runtime_info.input.experiment.project_name)"
export_if_set EXP_NAME               "$(cfg runtime_info.input.experiment.exp_name)"
export_if_set HARBOR_TRIALS_DIR      "$(abspath "$(cfg runtime_info.input.experiment.trials_dir)")"
export_if_set TRAJECTORY_LOGGER_SRC  "$(abspath "$(cfg runtime_info.input.experiment.trajectory_logger_src)")"

# Credentials
export_if_set WANDB_API_KEY "$(cfg runtime_info.input.credentials.wandb_api_key)"
export_if_set WANDB_MODE    "$(cfg runtime_info.input.credentials.wandb_mode)"

# ---------------------------------------------------------------------------
# Hand off to upstream — sync_1nodes_cc.sh handles vLLM/Ray/LiteLLM/verl wiring,
# applies its own defaults for anything we left empty, and resolves verl Hydra
# overrides from the env vars above.
# ---------------------------------------------------------------------------
echo "[block/train_1node_cc] config: $CONFIG"
echo "[block/train_1node_cc] upstream: $UPSTREAM"
exec bash "$UPSTREAM" "$@"
