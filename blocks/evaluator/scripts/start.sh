#!/usr/bin/env bash
# Run the configured Harbor evaluation command.
#
# Mirrors tracer/scripts/start.sh, but the evaluator block invokes Harbor with
# `--dataset <name>@<version> --registry-path …` so the benchmark is resolved from
# Harbor's registry (no local task copy). When task_source.no_hack is true,
# start.sh prepares the local swebench-verified-nohack registry and passes
# --agent-extra-allowed-host for the LiteLLM host.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${EVAL_CONFIG:-$BLOCK_DIR/config.yaml}"
export EVAL_CONFIG="$CONFIG"
LOG_DIR="$BLOCK_DIR/artifacts/logs"
PRINT_COMMAND_ONLY=0

# Captured early so the EXIT trap (defined later, combined with cleanup_litellm)
# can record the true start time even if the script fails mid-setup.
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/start.sh
  bash scripts/start.sh --update-repos
  bash scripts/start.sh --dry-run-command

Builds the default Harbor command from config.yaml and runs it inside
repos/harbor. Set EVAL_UPDATE_REPOS=1 or pass --update-repos to refresh Harbor
first. Add command_override in config.yaml only for special cases. Use
--dry-run-command to print the generated command without launching.
Before a real launch, a WARN (exit 77) from probe_llm_completion.sh blocks by
default; set EVAL_ALLOW_PROBE_WARN=1 only after explicitly accepting that warning.
EOF
}

UPDATE_REPOS="${EVAL_UPDATE_REPOS:-0}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --update-repos)
      UPDATE_REPOS=1
      shift
      ;;
    --dry-run-command)
      PRINT_COMMAND_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cfg() {
  python3 - "$CONFIG" "$1" <<'PY'
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required", file=sys.stderr)
    sys.exit(2)

config_path, dotted_key = sys.argv[1], sys.argv[2]
with open(config_path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}

value = data
for part in dotted_key.split("."):
    if not isinstance(value, dict):
        value = None
        break
    value = value.get(part)

if value is None:
    print("")
elif isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

abspath() {
  local p="$1"
  if [[ "$p" = /* ]]; then
    echo "$p"
  else
    echo "$BLOCK_DIR/$p"
  fi
}

write_litellm_config() {
  local output="$1"
  local template="$2"
  python3 - "$CONFIG" "$output" "$template" <<'PY'
from pathlib import Path
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required", file=sys.stderr)
    sys.exit(2)

config_path, output_path, template_path = sys.argv[1:4]
with open(config_path, encoding="utf-8") as fh:
    config = yaml.safe_load(fh) or {}

model_api = (config.get("runtime_info") or {}).get("input", {}).get("llm_api") or {}
proxy = (config.get("runtime_info") or {}).get("input", {}).get("litellm_proxy") or {}

if "api_base_url" in model_api and "api_base" not in model_api:
    model_api["api_base"] = model_api["api_base_url"]
if "model" in model_api and "upstream_model" not in model_api:
    model_api["upstream_model"] = model_api["model"]

data = {}
if template_path:
    template_file = Path(template_path)
    if not template_file.is_file():
        print(f"ERROR: LiteLLM config template not found: {template_path}", file=sys.stderr)
        sys.exit(2)
    with open(template_file, encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        print(f"ERROR: LiteLLM config template must be a mapping: {template_path}", file=sys.stderr)
        sys.exit(2)
    data.update(loaded)

model_name = (model_api.get("upstream_model") or "").split("/")[-1]
data["model_list"] = [
    {
        "model_name": model_name,
        "litellm_params": {
            "model": model_api.get("upstream_model"),
            "api_base": model_api.get("api_base"),
            "api_key": model_api.get("api_key"),
            "input_cost_per_token": model_api.get("input_cost_per_token"),
            "output_cost_per_token": model_api.get("output_cost_per_token"),
        },
    }
]
router_settings = data.get("router_settings") or {}
router_settings["optional_pre_call_checks"] = [
    "prompt_caching",
    "responses_api_deployment_check",
]
data["router_settings"] = router_settings
general_settings = data.get("general_settings") or {}
general_settings.update({
    "master_key": proxy.get("master_key"),
    "disable_spend_logs": True,
})
data["general_settings"] = general_settings
litellm_settings = data.get("litellm_settings") or {}
litellm_settings.update({
    "drop_params": True,
    "callbacks": "trajectory_logger.trajectory_logger",
    "use_chat_completions_url_for_anthropic_messages": True,
})
data["litellm_settings"] = litellm_settings

with open(output_path, "w", encoding="utf-8") as fh:
    yaml.safe_dump(data, fh, sort_keys=False)
PY
}

export_run_environment() {
  python3 - "$CONFIG" <<'PY'
import re
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required", file=sys.stderr)
    sys.exit(2)

with open(sys.argv[1], encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}

env = (((data.get("runtime_info") or {}).get("input") or {}).get("env_extra") or {})
if not isinstance(env, dict):
    print("ERROR: runtime_info.input.env_extra must be a mapping", file=sys.stderr)
    sys.exit(2)

for key, value in env.items():
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(key)):
        print(f"ERROR: invalid environment variable name: {key}", file=sys.stderr)
        sys.exit(2)
    if value is None:
        value = ""
    value = str(value).replace("\n", "\\n")
    print(f"{key}={value}")
PY
}

if [[ "$UPDATE_REPOS" == "1" ]]; then
  bash "$BLOCK_DIR/scripts/update_repos.sh"
fi

echo "=== evaluator preflight ==="
bash "$BLOCK_DIR/scripts/dryrun.sh"
echo ""

if [[ "$PRINT_COMMAND_ONLY" != "1" ]]; then
  echo "=== upstream completion launch gate ==="
  PROBE_RC=0
  bash "$BLOCK_DIR/scripts/probe_llm_completion.sh" || PROBE_RC=$?
  case "$PROBE_RC" in
    0)
      ;;
    77)
      if [[ "${EVAL_ALLOW_PROBE_WARN:-0}" != "1" ]]; then
        echo "ERROR: completion probe returned WARN; refusing to launch without explicit EVAL_ALLOW_PROBE_WARN=1" >&2
        exit 1
      fi
      echo "WARNING: continuing after an explicitly accepted completion-probe warning." >&2
      ;;
    *)
      echo "ERROR: completion probe failed; refusing to launch Harbor." >&2
      exit 1
      ;;
  esac
  echo ""
fi

HARBOR_PATH_RAW="$(cfg meta_info.repositories.harbor.path)"
RUN_COMMAND="$(cfg command_override)"
UV_PROJECT_ENVIRONMENT_RAW="$(cfg meta_info.environment.harbor_uv)"
LITELLM_UV_RAW="$(cfg meta_info.environment.litellm_uv)"
HARBOR_JOBS_DIR_RAW="$(cfg runtime_info.input.harbor_job.jobs_dir)"

DATASET_NAME="$(cfg runtime_info.input.task_source.dataset_name)"
DATASET_VERSION="$(cfg runtime_info.input.task_source.version)"
REGISTRY_RAW="$(cfg runtime_info.input.task_source.registry_path)"
if [[ -z "$REGISTRY_RAW" && -n "$HARBOR_PATH_RAW" ]]; then
  REGISTRY_RAW="$HARBOR_PATH_RAW/registry.json"
fi
NO_HACK="$(cfg runtime_info.input.task_source.no_hack)"
NO_HACK="${NO_HACK:-false}"

PRODUCER_BLOCK="$(cfg producer_block)"
if [[ -z "$PRODUCER_BLOCK" ]]; then
  PRODUCER_BLOCK="evaluator"
fi
OUTPUT_FORMAT="$(cfg output_format)"
if [[ -z "$OUTPUT_FORMAT" ]]; then
  OUTPUT_FORMAT="litellm_logger_v0.1"
fi
AGENT_NAME="$(cfg runtime_info.input.agent.name)"
AGENT_VERSION="$(cfg runtime_info.input.agent.version)"
AGENT_MODEL_NAME="$(cfg runtime_info.input.agent.model_name)"
if [[ -z "$AGENT_MODEL_NAME" ]]; then
  AGENT_MODEL_NAME="$(cfg runtime_info.input.llm_api.model)"
  if [[ -z "$AGENT_MODEL_NAME" ]]; then
    AGENT_MODEL_NAME="$(cfg runtime_info.input.llm_api.upstream_model)"
  fi
  AGENT_MODEL_NAME="${AGENT_MODEL_NAME##*/}"
fi
N_CONCURRENT="$(cfg runtime_info.input.harbor_job.n_concurrent)"
N_TASKS="$(cfg runtime_info.input.harbor_job.n_tasks)"
MAX_RETRIES="$(cfg runtime_info.input.harbor_job.max_retries)"
TIMEOUT_MULTIPLIER="$(cfg runtime_info.input.harbor_job.timeout_multiplier)"
MAX_TURNS="$(cfg runtime_info.input.agent.max_turns)"
TEMPERATURE="$(cfg runtime_info.input.agent.temperature)"

[[ -n "$HARBOR_PATH_RAW" ]] || { echo "ERROR: meta_info.repositories.harbor.path is empty" >&2; exit 1; }
[[ -n "$DATASET_NAME" ]] || { echo "ERROR: runtime_info.input.task_source.dataset_name is empty" >&2; exit 1; }
[[ -n "$REGISTRY_RAW" ]] || { echo "ERROR: registry path not configured (set task_source.registry_path)" >&2; exit 1; }
[[ -n "$AGENT_NAME" ]] || { echo "ERROR: runtime_info.input.agent.name is empty" >&2; exit 1; }
[[ -n "$AGENT_VERSION" ]] || { echo "ERROR: runtime_info.input.agent.version is empty" >&2; exit 1; }
[[ -n "$AGENT_MODEL_NAME" ]] || { echo "ERROR: could not derive agent model_name from runtime_info.input.llm_api.model" >&2; exit 1; }
[[ -n "$HARBOR_JOBS_DIR_RAW" ]] || { echo "ERROR: runtime_info.input.harbor_job.jobs_dir is empty" >&2; exit 1; }
[[ -n "$N_CONCURRENT" ]] || { echo "ERROR: runtime_info.input.harbor_job.n_concurrent is empty" >&2; exit 1; }
[[ -n "$MAX_RETRIES" ]] || { echo "ERROR: runtime_info.input.harbor_job.max_retries is empty" >&2; exit 1; }
[[ -n "$TIMEOUT_MULTIPLIER" ]] || { echo "ERROR: runtime_info.input.harbor_job.timeout_multiplier is empty" >&2; exit 1; }

HARBOR_DIR="$(abspath "$HARBOR_PATH_RAW")"
[[ -e "$HARBOR_DIR/.git" ]] || { echo "ERROR: $HARBOR_PATH_RAW missing; run bash scripts/update_repos.sh" >&2; exit 1; }

# no_hack: remap to the local hardened swebench-verified-nohack dataset + registry.
# Keeps config.yaml's dataset_name as the source benchmark (swebench-verified).
SOURCE_DATASET_NAME="$DATASET_NAME"
if [[ "$NO_HACK" == "true" ]]; then
  if [[ "$DATASET_NAME" != "swebench-verified" ]]; then
    echo "ERROR: task_source.no_hack requires dataset_name=swebench-verified (got $DATASET_NAME)." >&2
    echo "       -100 subsets are not supported with no_hack (the remap would silently run the full 500-task set);" >&2
    echo "       cap a smoke run with harbor_job.n_tasks instead." >&2
    exit 1
  fi
  PREPARE_ARGS=()
  if [[ -n "$N_TASKS" ]]; then
    PREPARE_ARGS+=(--limit "$N_TASKS")
  fi
  NOHACK_REGISTRY_ABS="$(bash "$BLOCK_DIR/scripts/prepare_nohack.sh" "${PREPARE_ARGS[@]}")"
  DATASET_NAME="swebench-verified-nohack"
  DATASET_VERSION="1.0"
  REGISTRY_RAW="$NOHACK_REGISTRY_ABS"
  echo "no_hack=true: using dataset=$DATASET_NAME registry=$NOHACK_REGISTRY_ABS (source=$SOURCE_DATASET_NAME)"
fi

HARBOR_DATASET_SPEC="$DATASET_NAME"
if [[ -n "$DATASET_VERSION" ]]; then
  HARBOR_DATASET_SPEC="${DATASET_NAME}@${DATASET_VERSION}"
fi
REGISTRY_ABS="$(abspath "$REGISTRY_RAW")"
[[ -f "$REGISTRY_ABS" ]] || { echo "ERROR: registry.json not found at $REGISTRY_RAW" >&2; exit 1; }

JOB_NAME_PREFIX="$(cfg runtime_info.input.harbor_job.job_name_prefix)"
if [[ -z "$JOB_NAME_PREFIX" ]]; then
  JOB_NAME_PREFIX="${DATASET_NAME}-${AGENT_NAME}-${AGENT_VERSION}-${AGENT_MODEL_NAME}"
fi
JOB_NAME="${JOB_NAME_PREFIX}-$(date +%Y%m%d%H%M%S)"
JOB_DIR_RAW="$(cfg job_dir)"
if [[ -z "$JOB_DIR_RAW" ]]; then
  JOB_DIR_RAW="${HARBOR_JOBS_DIR_RAW%/}/$JOB_NAME"
fi
[[ -n "$JOB_DIR_RAW" ]] || { echo "ERROR: job_dir is empty" >&2; exit 1; }
if [[ -n "$UV_PROJECT_ENVIRONMENT_RAW" ]]; then
  export UV_PROJECT_ENVIRONMENT="$(abspath "$UV_PROJECT_ENVIRONMENT_RAW")"
fi
if [[ -n "${UV_PROJECT_ENVIRONMENT:-}" ]]; then
  HARBOR_PYTHON="$UV_PROJECT_ENVIRONMENT/bin/python"
else
  HARBOR_PYTHON="python3"
fi
[[ -x "$HARBOR_PYTHON" ]] || { echo "ERROR: Harbor Python not found or not executable: $HARBOR_PYTHON" >&2; exit 1; }
AGENT_NAME="$(cfg runtime_info.input.agent.name)"
export EVAL_AGENT_IMPORT_PATH="$(cfg runtime_info.input.agent.import_path)"
if [[ -z "$EVAL_AGENT_IMPORT_PATH" ]]; then
  case "$AGENT_NAME" in
    custom-claude-code)
      export EVAL_AGENT_IMPORT_PATH="harbor.agents.custom.claude_code:CustomClaudeCode"
      ;;
    custom-openhands-sdk)
      export EVAL_AGENT_IMPORT_PATH="harbor.agents.custom.openhands_sdk:CustomOpenHandsSDK"
      ;;
    custom-opencode)
      export EVAL_AGENT_IMPORT_PATH="harbor.agents.custom.opencode:CustomOpenCode"
      ;;
  esac
fi
export EVAL_AGENT_API_PROTOCOL="$(cfg runtime_info.input.agent.api_protocol)"
if [[ -z "$EVAL_AGENT_API_PROTOCOL" ]]; then
  case "$AGENT_NAME" in
    custom-claude-code)    export EVAL_AGENT_API_PROTOCOL="anthropic" ;;
    custom-openhands-sdk)  export EVAL_AGENT_API_PROTOCOL="openai" ;;
    custom-opencode)       export EVAL_AGENT_API_PROTOCOL="openai" ;;
  esac
fi
export EVAL_AGENT_MODEL_NAME="$AGENT_MODEL_NAME"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/evaluator_$(date +%Y-%m-%d_%H-%M-%S).log"

JOB_DIR="$(abspath "$JOB_DIR_RAW")"
HARBOR_JOBS_DIR="$(abspath "$HARBOR_JOBS_DIR_RAW")"
mkdir -p "$HARBOR_JOBS_DIR" "$JOB_DIR"
cp "$CONFIG" "$JOB_DIR/config.yaml"

export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export EVAL_ARTIFACTS_DIR="$BLOCK_DIR/artifacts"
export EVAL_HARBOR_DATASET_NAME="$DATASET_NAME"
export EVAL_HARBOR_DATASET_VERSION="$DATASET_VERSION"
export EVAL_HARBOR_REGISTRY_PATH="$REGISTRY_ABS"
export EVAL_HARBOR_JOBS_DIR="$HARBOR_JOBS_DIR"
export EVAL_JOB_NAME="$JOB_NAME"
export EVAL_JOB_NAME_PREFIX="$JOB_NAME_PREFIX"
export EVAL_JOB_DIR="$JOB_DIR"
export EVAL_TRAJECTORY_FILE_PATTERN="$JOB_DIR/<task-id>/agent/litellm-trajectory.jsonl"
export EVAL_RUNTIME_ROOT="$(cfg runtime_info.input.runtime_mount.container_runtime_root)"
if [[ -z "$EVAL_RUNTIME_ROOT" ]]; then
  case "$AGENT_NAME" in
    custom-claude-code)    export EVAL_RUNTIME_ROOT="/opt/custom-agent-runtime/claude-code" ;;
    custom-openhands-sdk)  export EVAL_RUNTIME_ROOT="/opt/custom-agent-runtime/oh-sdk" ;;
    custom-opencode)       export EVAL_RUNTIME_ROOT="/opt/custom-agent-runtime/opencode" ;;
  esac
fi
export EVAL_RUNTIME_SOURCE_IMAGE="$(cfg runtime_info.input.agent.runtime_image)"
RUNTIME_HOST_PATH_RAW="$(cfg runtime_info.input.agent.runtime_host_path)"
if [[ -n "$RUNTIME_HOST_PATH_RAW" ]]; then
  export EVAL_RUNTIME_HOST_PATH="$(abspath "$RUNTIME_HOST_PATH_RAW")"
else
  export EVAL_RUNTIME_HOST_PATH=""
fi
export EVAL_RUNTIME_IMAGE_SUBPATH="$(cfg runtime_info.input.runtime_mount.image_subpath)"
if [[ -z "$EVAL_RUNTIME_IMAGE_SUBPATH" ]]; then
  EVAL_RUNTIME_IMAGE_SUBPATH="${EVAL_RUNTIME_ROOT#/}"
  export EVAL_RUNTIME_IMAGE_SUBPATH
fi
export EVAL_CUSTOM_AGENT_RUNTIME_ROOT="$EVAL_RUNTIME_ROOT"
export EVAL_CUSTOM_AGENT_RUNTIME_ENV_SCRIPT="$EVAL_RUNTIME_ROOT/runtime-env.sh"
case "$AGENT_NAME" in
  custom-claude-code)    export EVAL_CUSTOM_AGENT_CLAUDE="$EVAL_RUNTIME_ROOT/bin/claude" ;;
  custom-openhands-sdk)  export EVAL_CUSTOM_AGENT_PYTHON="$EVAL_RUNTIME_ROOT/bin/python" ;;
  custom-opencode)       export EVAL_CUSTOM_AGENT_OPENCODE="$EVAL_RUNTIME_ROOT/bin/opencode" ;;
esac
LITELLM_HOST_IP="${LITELLM_HOST_IP:-$(hostname -I | awk '{print $1}')}"
[[ -n "$LITELLM_HOST_IP" ]] || { echo "ERROR: could not determine LiteLLM host IP; set LITELLM_HOST_IP explicitly" >&2; exit 1; }
LITELLM_PORT_RESOLVED="${LITELLM_PORT:-$(cfg runtime_info.input.litellm_proxy.port)}"
export EVAL_LITELLM_ANTHROPIC_BASE_URL="http://$LITELLM_HOST_IP:$LITELLM_PORT_RESOLVED"
export EVAL_LITELLM_OPENAI_BASE_URL="http://$LITELLM_HOST_IP:$LITELLM_PORT_RESOLVED/v1"
export EVAL_LITELLM_MASTER_KEY="$(cfg runtime_info.input.litellm_proxy.master_key)"
if [[ -n "$LITELLM_UV_RAW" ]]; then
  LITELLM_UV="$(abspath "$LITELLM_UV_RAW")"
  export EVAL_LITELLM_PYTHON="$LITELLM_UV/bin/python"
  export EVAL_LITELLM_BIN="$LITELLM_UV/bin/litellm"
else
  export EVAL_LITELLM_PYTHON="python"
  export EVAL_LITELLM_BIN="litellm"
fi
while IFS= read -r env_line; do
  [[ -n "$env_line" ]] && export "$env_line"
done < <(export_run_environment)

if [[ -z "$RUN_COMMAND" ]]; then
  [[ -n "$EVAL_AGENT_IMPORT_PATH" ]] || { echo "ERROR: runtime_info.input.agent.import_path is empty and no default is defined for agent.name=$AGENT_NAME" >&2; exit 1; }
  EXTRA_ARGS=""
  if [[ -n "$N_TASKS" ]]; then
    EXTRA_ARGS=" --n-tasks $(printf '%q' "$N_TASKS")"
  fi
  # Never retry timed-out tasks — they'll just time out again and waste budget
  EXTRA_ARGS="$EXTRA_ARGS --retry-exclude AgentTimeoutError"
  # Add any extra exclude-task-name flags from HARBOR_EXCLUDE_TASKS (space-separated list)
  if [[ -n "${HARBOR_EXCLUDE_TASKS:-}" ]]; then
    for _excl_task in $HARBOR_EXCLUDE_TASKS; do
      EXTRA_ARGS="$EXTRA_ARGS --exclude-task-name $(printf '%q' "$_excl_task")"
    done
  fi
  # no_hack: allow agent egress only to the LiteLLM host (task.toml allowlist is empty by default).
  if [[ "$NO_HACK" == "true" ]]; then
    AGENT_ALLOWED_LLM_HOST="${AGENT_ALLOWED_LLM_HOST:-$LITELLM_HOST_IP}"
    EXTRA_ARGS="$EXTRA_ARGS --agent-extra-allowed-host $(printf '%q' "$AGENT_ALLOWED_LLM_HOST")"
  fi
  # Per-agent kwargs/env flags. MAX_TURNS is reused as max_iterations for openhands-sdk (same semantics).
  # openhands-sdk uses litellm internally and needs a provider-prefixed model name.
  case "$AGENT_NAME" in
    custom-openhands-sdk)
      HARBOR_MODEL_NAME="openai/$EVAL_AGENT_MODEL_NAME"
      ;;
    custom-opencode)
      HARBOR_MODEL_NAME="hosted_vllm/$EVAL_AGENT_MODEL_NAME"
      ;;
    *)
      HARBOR_MODEL_NAME="$EVAL_AGENT_MODEL_NAME"
      ;;
  esac
  case "$AGENT_NAME" in
    custom-claude-code)
      AGENT_FLAGS=" --ak max_turns=$(printf '%q' "$MAX_TURNS") --ak temperature=$(printf '%q' "$TEMPERATURE")"
      AGENT_FLAGS+=" --ae ANTHROPIC_BASE_URL=\$EVAL_LITELLM_ANTHROPIC_BASE_URL"
      AGENT_FLAGS+=" --ae ANTHROPIC_API_KEY=\$EVAL_LITELLM_MASTER_KEY"
      AGENT_FLAGS+=" --ae ANTHROPIC_MODEL=\$EVAL_AGENT_MODEL_NAME"
      AGENT_FLAGS+=" --ae CUSTOM_AGENT_RUNTIME_ROOT=\$EVAL_CUSTOM_AGENT_RUNTIME_ROOT"
      AGENT_FLAGS+=" --ae CUSTOM_AGENT_CLAUDE=\$EVAL_CUSTOM_AGENT_CLAUDE"
      AGENT_FLAGS+=" --ae CUSTOM_AGENT_RUNTIME_ENV_SCRIPT=\$EVAL_CUSTOM_AGENT_RUNTIME_ENV_SCRIPT"
      AGENT_FLAGS+=" --ae CLAUDE_CODE_ATTRIBUTION_HEADER=0"
      ;;
    custom-openhands-sdk)
      AGENT_FLAGS=" --ak max_iterations=$(printf '%q' "$MAX_TURNS") --ak temperature=$(printf '%q' "$TEMPERATURE")"
      AGENT_FLAGS+=" --ae LLM_BASE_URL=\$EVAL_LITELLM_OPENAI_BASE_URL"
      AGENT_FLAGS+=" --ae LLM_API_KEY=\$EVAL_LITELLM_MASTER_KEY"
      AGENT_FLAGS+=" --ae CUSTOM_AGENT_RUNTIME_ROOT=\$EVAL_CUSTOM_AGENT_RUNTIME_ROOT"
      AGENT_FLAGS+=" --ae CUSTOM_AGENT_PYTHON=\$EVAL_CUSTOM_AGENT_PYTHON"
      ;;
    custom-opencode)
      OPENCODE_DISABLE_STREAMING="${OPENCODE_DISABLE_STREAMING:-true}"
      OPENCODE_CONFIG_CONTENT="$($HARBOR_PYTHON -c 'import json, os
model_name = os.environ["EVAL_AGENT_MODEL_NAME"]
opencode_model = "hosted_vllm/" + model_name
disable_streaming = os.environ.get("OPENCODE_DISABLE_STREAMING", "true").lower() in {"1", "true", "yes", "on"}
options = {
    "baseURL": "{env:HOSTED_VLLM_BASE_URL}",
    "apiKey": "{env:HOSTED_VLLM_API_KEY}",
    "headers": {"x-harbor-temperature": "{env:OPENCODE_TEMPERATURE}"},
}
if disable_streaming:
    options["disableStreaming"] = True
print(json.dumps({
    "$schema": "https://opencode.ai/config.json",
    "model": opencode_model,
    "small_model": opencode_model,
    "enabled_providers": ["hosted_vllm"],
    "provider": {
        "hosted_vllm": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Hosted vLLM",
            "options": options,
            "models": {model_name: {"name": model_name}},
        },
    },
    "agent": {
        "title": {"model": opencode_model},
        "summary": {"model": opencode_model},
        "compaction": {"model": opencode_model},
    },
}, separators=(",", ":")))'
      )"
      AGENT_FLAGS=""
      AGENT_FLAGS+=" --ae HOSTED_VLLM_BASE_URL=\$EVAL_LITELLM_OPENAI_BASE_URL"
      AGENT_FLAGS+=" --ae HOSTED_VLLM_API_KEY=\$EVAL_LITELLM_MASTER_KEY"
      AGENT_FLAGS+=" --ae OPENCODE_TEMPERATURE=$TEMPERATURE"
      AGENT_FLAGS+=" --ae OPENCODE_CONFIG_CONTENT=$(printf '%q' "$OPENCODE_CONFIG_CONTENT")"
      AGENT_FLAGS+=" --ae CUSTOM_AGENT_RUNTIME_ROOT=\$EVAL_CUSTOM_AGENT_RUNTIME_ROOT"
      AGENT_FLAGS+=" --ae CUSTOM_AGENT_OPENCODE=\$EVAL_CUSTOM_AGENT_OPENCODE"
      AGENT_FLAGS+=" --ae CUSTOM_AGENT_RUNTIME_ENV_SCRIPT=\$EVAL_CUSTOM_AGENT_RUNTIME_ENV_SCRIPT"
      ;;
    *)
      echo "ERROR: no default agent flags for agent.name=$AGENT_NAME (set command_override or extend start.sh)" >&2; exit 1 ;;
  esac
  RUN_COMMAND="uv run harbor run --dataset $(printf '%q' "$HARBOR_DATASET_SPEC") --registry-path $(printf '%q' "$EVAL_HARBOR_REGISTRY_PATH") --jobs-dir $(printf '%q' "$HARBOR_JOBS_DIR") --agent-import-path $(printf '%q' "$EVAL_AGENT_IMPORT_PATH") --job-name $(printf '%q' "$EVAL_JOB_NAME") --mounts-json \"\$($(printf '%q' "$HARBOR_PYTHON") - <<'PY'
import json
import os
host_path = os.environ.get('EVAL_RUNTIME_HOST_PATH', '')
if host_path:
    mounts = [{
        'type': 'bind',
        'source': host_path,
        'target': os.environ['EVAL_RUNTIME_ROOT'],
        'read_only': True,
    }]
else:
    mounts = [{
        'type': 'image',
        'source': os.environ['EVAL_RUNTIME_SOURCE_IMAGE'],
        'target': os.environ['EVAL_RUNTIME_ROOT'],
        'read_only': True,
        'image': {'subpath': os.environ['EVAL_RUNTIME_IMAGE_SUBPATH'].lstrip('/')},
    }]
print(json.dumps(mounts))
PY
)\" --model $(printf '%q' "$HARBOR_MODEL_NAME") --n-concurrent $(printf '%q' "$N_CONCURRENT")${EXTRA_ARGS} --timeout-multiplier $(printf '%q' "$TIMEOUT_MULTIPLIER") --max-retries $(printf '%q' "$MAX_RETRIES") --ak version=$(printf '%q' "$AGENT_VERSION")${AGENT_FLAGS}"
fi

LITELLM_TEMPLATE_RAW="$(cfg runtime_info.input.litellm_proxy.config_template)"
[[ -n "$LITELLM_TEMPLATE_RAW" ]] || { echo "ERROR: runtime_info.input.litellm_proxy.config_template is empty" >&2; exit 1; }
if [[ "$LITELLM_TEMPLATE_RAW" = /* ]]; then
  LITELLM_TEMPLATE_PATH="$LITELLM_TEMPLATE_RAW"
else
  LITELLM_TEMPLATE_PATH="$HARBOR_DIR/$LITELLM_TEMPLATE_RAW"
fi
[[ -f "$LITELLM_TEMPLATE_PATH" ]] || { echo "ERROR: LiteLLM config template not found: $LITELLM_TEMPLATE_RAW" >&2; exit 1; }
LITELLM_CONFIG_NAME="$(basename "$LITELLM_TEMPLATE_RAW")"
LITELLM_CONFIG_NAME="${LITELLM_CONFIG_NAME%.example.yaml}"
LITELLM_CONFIG_NAME="${LITELLM_CONFIG_NAME}_evaluator"
LITELLM_ARTIFACT_DIR="$BLOCK_DIR/artifacts/litellm/$JOB_NAME"
mkdir -p "$LITELLM_ARTIFACT_DIR"
LITELLM_CONFIG_PATH="$LITELLM_ARTIFACT_DIR/${LITELLM_CONFIG_NAME}.yaml"
TRAJECTORY_LOGGER_SOURCE="$HARBOR_DIR/scripts/serve_llm/trajectory_logger.py"
[[ -f "$TRAJECTORY_LOGGER_SOURCE" ]] || { echo "ERROR: trajectory_logger.py not found in Harbor checkout" >&2; exit 1; }
cp "$TRAJECTORY_LOGGER_SOURCE" "$LITELLM_ARTIFACT_DIR/trajectory_logger.py"
write_litellm_config "$LITELLM_CONFIG_PATH" "$LITELLM_TEMPLATE_PATH"
export EVAL_LITELLM_CONFIG="$LITELLM_CONFIG_PATH"

if [[ "$PRINT_COMMAND_ONLY" == "1" ]]; then
  echo "=== evaluator generated command ==="
  echo "LiteLLM config: $LITELLM_CONFIG_PATH"
  echo "$RUN_COMMAND"
  exit 0
fi

echo "=== starting LiteLLM proxy ===" | tee "$LOG_FILE"
# Default to a single proxy worker. The eval proxy is pure async I/O in front of
# one upstream backend, so 1 worker comfortably serves n_concurrent tasks. More
# importantly, multi-worker boot crash-loops on slow/network filesystems: each
# worker re-imports litellm (~60s on /mnt/public), which exceeds uvicorn's
# multiprocess boot tolerance and every child dies ("Child process [...] died"),
# so the port never serves. Override with LITELLM_NUM_WORKERS=N if needed.
export LITELLM_NUM_WORKERS="${LITELLM_NUM_WORKERS:-1}"
setsid bash -c '
  cd "$1"
  PATH="$(dirname "$EVAL_LITELLM_BIN"):$PATH" \
    LITELLM_CONFIG="$2" \
    LITELLM_LOG_FOLDER="$3/logs" \
    LITELLM_PORT="$4" \
    API_KEY="$5" \
    LITELLM_NUM_WORKERS="$6" \
    bash scripts/serve_llm/serve_litellm.sh
' bash "$HARBOR_DIR" "$LITELLM_CONFIG_PATH" "$LITELLM_ARTIFACT_DIR" "${LITELLM_PORT:-$(cfg runtime_info.input.litellm_proxy.port)}" "$EVAL_LITELLM_MASTER_KEY" "$LITELLM_NUM_WORKERS" >>"$LOG_FILE" 2>&1 &
LITELLM_PID="$!"
cleanup_litellm() {
  if kill -0 "$LITELLM_PID" >/dev/null 2>&1; then
    kill -- "-$LITELLM_PID" >/dev/null 2>&1 || kill "$LITELLM_PID" >/dev/null 2>&1 || true
    wait "$LITELLM_PID" >/dev/null 2>&1 || true
  fi
}
_archive_run_on_exit() {
    local rc=$?
    cleanup_litellm || true
    bash "$BLOCK_DIR/scripts/archive_run.sh" "$rc" "$RUN_STARTED_AT" || true
    exit $rc
}
trap _archive_run_on_exit EXIT

# The LiteLLM proxy must be listening before Harbor launches, otherwise the
# first task's LLM call hits a dead port. Importing litellm can take ~30-60s on
# cold/network filesystems, so wait generously (and fail loudly if the proxy
# never binds — never fall through to Harbor against a dead proxy).
PROXY_READY=0
for _ in {1..120}; do
  if "$EVAL_LITELLM_PYTHON" - "$EVAL_LITELLM_ANTHROPIC_BASE_URL" <<'PY' >/dev/null 2>&1
import socket
import sys
from urllib.parse import urlparse

u = urlparse(sys.argv[1])
host = u.hostname or "127.0.0.1"
port = u.port or (443 if u.scheme == "https" else 80)
with socket.create_connection((host, port), timeout=2):
    pass
PY
  then
    PROXY_READY=1
    break
  fi
  if ! kill -0 "$LITELLM_PID" >/dev/null 2>&1; then
    echo "ERROR: LiteLLM proxy exited before Harbor job started" | tee -a "$LOG_FILE"
    echo "       See proxy log under: $LITELLM_ARTIFACT_DIR/logs" | tee -a "$LOG_FILE"
    exit 1
  fi
  sleep 1
done
if [[ "$PROXY_READY" != "1" ]]; then
  echo "ERROR: LiteLLM proxy did not start listening on $EVAL_LITELLM_ANTHROPIC_BASE_URL in time" | tee -a "$LOG_FILE"
  echo "       See proxy log under: $LITELLM_ARTIFACT_DIR/logs" | tee -a "$LOG_FILE"
  exit 1
fi
echo "LiteLLM proxy is listening on $EVAL_LITELLM_ANTHROPIC_BASE_URL" | tee -a "$LOG_FILE"

echo "=== evaluator start ===" | tee -a "$LOG_FILE"
echo "Harbor dir: $HARBOR_PATH_RAW" | tee -a "$LOG_FILE"
echo "Producer:   $PRODUCER_BLOCK" | tee -a "$LOG_FILE"
echo "Format:     $OUTPUT_FORMAT" | tee -a "$LOG_FILE"
echo "Dataset:    $HARBOR_DATASET_SPEC" | tee -a "$LOG_FILE"
echo "Registry:   $REGISTRY_RAW" | tee -a "$LOG_FILE"
echo "Jobs dir:   $HARBOR_JOBS_DIR_RAW" | tee -a "$LOG_FILE"
echo "Job prefix: $JOB_NAME_PREFIX" | tee -a "$LOG_FILE"
echo "Job dir:    $JOB_DIR_RAW" | tee -a "$LOG_FILE"
echo "Traj files: $EVAL_TRAJECTORY_FILE_PATTERN" | tee -a "$LOG_FILE"
echo "Config:     $JOB_DIR_RAW/config.yaml" | tee -a "$LOG_FILE"
echo "Command:    $RUN_COMMAND" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

set +e
(cd "$HARBOR_DIR" && bash -lc "$RUN_COMMAND") 2>&1 | tee -a "$LOG_FILE"
PIPE_STATUS=("${PIPESTATUS[@]}")
set -e
HARBOR_RC="${PIPE_STATUS[0]:-1}"
TEE_RC="${PIPE_STATUS[1]:-1}"
RUN_RC="$HARBOR_RC"
[[ "$RUN_RC" == "0" && "$TEE_RC" != "0" ]] && RUN_RC="$TEE_RC"

echo "" | tee -a "$LOG_FILE"
if [[ "$RUN_RC" == "0" ]]; then
  echo "evaluator complete. Expected job dir: $JOB_DIR_RAW" | tee -a "$LOG_FILE"
else
  echo "WARNING: Harbor command exited with rc=$HARBOR_RC (tee rc=$TEE_RC); analyzing any partial results before exit." | tee -a "$LOG_FILE"
fi
echo "Expected trajectory files: $EVAL_TRAJECTORY_FILE_PATTERN" | tee -a "$LOG_FILE"

# Post-hoc job analysis: write attribution/scoring into <job_dir>/analysis/ so the
# dashboard can render it. Opt-out via runtime_info.input.job_analysis.enabled: false.
# Non-fatal by design — a failed analysis must never fail a completed eval run.
JOB_ANALYSIS_ENABLED="$(cfg runtime_info.input.job_analysis.enabled)"
if [[ "$JOB_ANALYSIS_ENABLED" == "false" ]]; then
  echo "" | tee -a "$LOG_FILE"
  echo "job analysis disabled (runtime_info.input.job_analysis.enabled: false); skipping" | tee -a "$LOG_FILE"
elif [[ "$RUN_RC" != "0" ]] \
    && [[ -z "$(find "$JOB_DIR" -mindepth 2 -maxdepth 2 -type f -name result.json -print -quit 2>/dev/null)" ]]; then
  echo "" | tee -a "$LOG_FILE"
  echo "job analysis skipped: Harbor failed before any per-trial result was written" | tee -a "$LOG_FILE"
else
  echo "" | tee -a "$LOG_FILE"
  echo "=== running post-eval job analysis ===" | tee -a "$LOG_FILE"
  # Partial-result analysis must not turn a failed Harbor run into an expensive
  # full dataset download/tagging job. It may reuse existing gold only.
  [[ "$RUN_RC" == "0" ]] || export JOB_ANALYSIS_PREPARE_DATASET=0
  bash "$BLOCK_DIR/scripts/analyze_job.sh" "$JOB_DIR" 2>&1 | tee -a "$LOG_FILE" || \
    echo "WARNING: job analysis failed; eval results are unaffected. See log above." | tee -a "$LOG_FILE"
fi

if [[ "$RUN_RC" != "0" ]]; then
  exit "$RUN_RC"
fi
