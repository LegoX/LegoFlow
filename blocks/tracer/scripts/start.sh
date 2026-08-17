#!/usr/bin/env bash
# Run the configured Harbor trajectory generation command.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"
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
repos/harbor. Set TRAJGEN_UPDATE_REPOS=1 or pass --update-repos to refresh
Harbor first. Add command_override in config.yaml only for special cases.
Use --dry-run-command to print the generated command without launching.
EOF
}

UPDATE_REPOS="${TRAJGEN_UPDATE_REPOS:-0}"
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

cfg_literal() {
  python3 - "$CONFIG" "$1" <<'PY'
import shlex
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
    print(shlex.quote(str(value)))
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

# normalize key names: api_base_url → api_base, model → upstream_model
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

# Stage by default. Preflight no longer fails on an unstaged block — an unstaged
# block is simply one that has not run yet — so a launch has to do the staging
# itself, or Harbor is pointed at a directory nobody filled. Staging is
# idempotent (a valid dataset directory is reused) and, now that a local pool is
# linked rather than copied, close to free. Set TRAJGEN_PREPARE_TASKS=0 to skip.
if [[ "${TRAJGEN_PREPARE_TASKS:-1}" != "0" ]]; then
  bash "$BLOCK_DIR/scripts/prepare_tasks.sh" --config "$CONFIG"
fi

echo "=== tracer preflight ==="
bash "$BLOCK_DIR/scripts/dryrun.sh"
echo ""

HARBOR_PATH_RAW="$(cfg meta_info.repositories.harbor.path)"
RUN_COMMAND="$(cfg command_override)"
UV_PROJECT_ENVIRONMENT_RAW="$(cfg meta_info.environment.harbor_uv)"
LITELLM_UV_RAW="$(cfg meta_info.environment.litellm_uv)"
HARBOR_DATASET_PATH_RAW="$(cfg runtime_info.input.harbor_job.dataset_path)"
HARBOR_JOBS_DIR_RAW="$(cfg runtime_info.input.harbor_job.jobs_dir)"
HARBOR_DATASET="$(cfg runtime_info.input.harbor_job.dataset)"
TASK_SOURCE_DATASET_NAME="$(cfg runtime_info.input.task_source.dataset_name)"
if [[ -z "$HARBOR_DATASET" && -n "$TASK_SOURCE_DATASET_NAME" ]]; then
  HARBOR_DATASET="$(basename "$TASK_SOURCE_DATASET_NAME")"
fi
if [[ -z "$HARBOR_DATASET_PATH_RAW" && -n "$HARBOR_DATASET" ]]; then
  HARBOR_DATASET_PATH_RAW="artifacts/tasks/$HARBOR_DATASET"
fi
PRODUCER_BLOCK="$(cfg producer_block)"
if [[ -z "$PRODUCER_BLOCK" ]]; then
  PRODUCER_BLOCK="tracer"
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
  # Litellm-style scaffolds (openhands-sdk) need the provider/ prefix kept on
  # the model string so their own litellm client can route the request; other
  # scaffolds (--model, ANTHROPIC_MODEL) want the bare alias, so keep both.
  AGENT_MODEL_FULL="$AGENT_MODEL_NAME"
  AGENT_MODEL_NAME="${AGENT_MODEL_NAME##*/}"
else
  AGENT_MODEL_FULL="$AGENT_MODEL_NAME"
fi
JOB_NAME_PREFIX="$(cfg runtime_info.input.harbor_job.job_name_prefix)"
if [[ -z "$JOB_NAME_PREFIX" ]]; then
  JOB_NAME_PREFIX="${HARBOR_DATASET}-${AGENT_NAME}-${AGENT_VERSION}-${AGENT_MODEL_NAME}"
fi
JOB_NAME="${JOB_NAME_PREFIX}-$(date +%Y%m%d%H%M%S)"
JOB_DIR_RAW="$(cfg job_dir)"
if [[ -z "$JOB_DIR_RAW" ]]; then
  JOB_DIR_RAW="${HARBOR_JOBS_DIR_RAW%/}/$JOB_NAME"
fi
N_CONCURRENT="$(cfg runtime_info.input.harbor_job.n_concurrent)"
N_TASKS="$(cfg runtime_info.input.harbor_job.n_tasks)"
MAX_RETRIES="$(cfg runtime_info.input.harbor_job.max_retries)"
TIMEOUT_MULTIPLIER="$(cfg runtime_info.input.harbor_job.timeout_multiplier)"
MAX_TURNS="$(cfg runtime_info.input.agent.max_turns)"
TEMPERATURE="$(cfg runtime_info.input.agent.temperature)"

[[ -n "$HARBOR_PATH_RAW" ]] || { echo "ERROR: meta_info.repositories.harbor.path is empty" >&2; exit 1; }
[[ -n "$PRODUCER_BLOCK" ]] || { echo "ERROR: producer_block is empty" >&2; exit 1; }
[[ -n "$OUTPUT_FORMAT" ]] || { echo "ERROR: output_format is empty" >&2; exit 1; }
[[ -n "$HARBOR_DATASET" ]] || { echo "ERROR: could not derive dataset from runtime_info.input.task_source.dataset_name" >&2; exit 1; }
[[ -n "$AGENT_NAME" ]] || { echo "ERROR: runtime_info.input.agent.name is empty" >&2; exit 1; }
[[ -n "$AGENT_VERSION" ]] || { echo "ERROR: runtime_info.input.agent.version is empty" >&2; exit 1; }
[[ -n "$AGENT_MODEL_NAME" ]] || { echo "ERROR: could not derive agent model_name from runtime_info.input.llm_api.model" >&2; exit 1; }
[[ -n "$HARBOR_DATASET_PATH_RAW" ]] || { echo "ERROR: could not derive dataset path from runtime_info.input.task_source.dataset_name" >&2; exit 1; }
[[ -n "$HARBOR_JOBS_DIR_RAW" ]] || { echo "ERROR: runtime_info.input.harbor_job.jobs_dir is empty" >&2; exit 1; }
[[ -n "$N_CONCURRENT" ]] || { echo "ERROR: runtime_info.input.harbor_job.n_concurrent is empty" >&2; exit 1; }
[[ -n "$MAX_RETRIES" ]] || { echo "ERROR: runtime_info.input.harbor_job.max_retries is empty" >&2; exit 1; }
[[ -n "$TIMEOUT_MULTIPLIER" ]] || { echo "ERROR: runtime_info.input.harbor_job.timeout_multiplier is empty" >&2; exit 1; }
[[ -n "$JOB_DIR_RAW" ]] || { echo "ERROR: job_dir is empty" >&2; exit 1; }

HARBOR_DIR="$(abspath "$HARBOR_PATH_RAW")"
[[ -e "$HARBOR_DIR/.git" ]] || { echo "ERROR: $HARBOR_PATH_RAW missing; run bash scripts/update_repos.sh" >&2; exit 1; }
if [[ -n "$UV_PROJECT_ENVIRONMENT_RAW" ]]; then
  export UV_PROJECT_ENVIRONMENT="$(abspath "$UV_PROJECT_ENVIRONMENT_RAW")"
fi
if [[ -n "${UV_PROJECT_ENVIRONMENT:-}" ]]; then
  HARBOR_PYTHON="$UV_PROJECT_ENVIRONMENT/bin/python"
else
  HARBOR_PYTHON="python3"
fi
[[ -x "$HARBOR_PYTHON" ]] || { echo "ERROR: Harbor Python not found or not executable: $HARBOR_PYTHON" >&2; exit 1; }
export TRAJGEN_AGENT_IMPORT_PATH="$(cfg runtime_info.input.agent.import_path)"
if [[ -z "$TRAJGEN_AGENT_IMPORT_PATH" ]]; then
  case "$AGENT_NAME" in
    custom-claude-code)   export TRAJGEN_AGENT_IMPORT_PATH="harbor.agents.custom.claude_code:CustomClaudeCode" ;;
    custom-opencode)      export TRAJGEN_AGENT_IMPORT_PATH="harbor.agents.custom.opencode:CustomOpenCode" ;;
    custom-openhands-sdk) export TRAJGEN_AGENT_IMPORT_PATH="harbor.agents.custom.openhands_sdk:CustomOpenHandsSDK" ;;
  esac
fi
export TRAJGEN_AGENT_API_PROTOCOL="$(cfg runtime_info.input.agent.api_protocol)"
if [[ -z "$TRAJGEN_AGENT_API_PROTOCOL" && "$(cfg runtime_info.input.agent.name)" == "custom-claude-code" ]]; then
  export TRAJGEN_AGENT_API_PROTOCOL="anthropic"
fi
export TRAJGEN_AGENT_MODEL_NAME="$AGENT_MODEL_NAME"
export TRAJGEN_AGENT_MODEL_NAME_FULL="$AGENT_MODEL_FULL"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/tracer_$(date +%Y-%m-%d_%H-%M-%S).log"

JOB_DIR="$(abspath "$JOB_DIR_RAW")"
HARBOR_DATASET_PATH="$(abspath "$HARBOR_DATASET_PATH_RAW")"
HARBOR_JOBS_DIR="$(abspath "$HARBOR_JOBS_DIR_RAW")"
mkdir -p "$(dirname "$HARBOR_DATASET_PATH")" "$HARBOR_JOBS_DIR" "$JOB_DIR"

# The launch-time gate. Preflight deliberately does not fail on missing staging,
# so this is the last point where an empty dataset can be caught: without it
# Harbor runs against a directory that does not exist and reports no work rather
# than an error, which reads as "nothing to do" instead of "nothing was staged".
if ! "${PYTHON:-python3}" - "$HARBOR_DATASET_PATH" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
if not root.is_dir():
    print(f"ERROR: task directory does not exist: {root}", file=sys.stderr)
    raise SystemExit(1)
tasks = [p for p in root.iterdir() if (p / "task.toml").is_file()]
if not tasks:
    print(f"ERROR: no Harbor tasks under {root}", file=sys.stderr)
    raise SystemExit(1)
print(f"Tasks:      {len(tasks)} staged at {root}")
PY
then
  echo "ERROR: refusing to launch Harbor with no tasks staged." >&2
  echo "       Check runtime_info.input.task_source, then run: bash scripts/prepare_tasks.sh" >&2
  exit 1
fi
cp "$CONFIG" "$JOB_DIR/config.yaml"

export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export TRAJGEN_ARTIFACTS_DIR="$BLOCK_DIR/artifacts"
export TRAJGEN_HARBOR_DATASET_PATH="$HARBOR_DATASET_PATH"
export TRAJGEN_HARBOR_JOBS_DIR="$HARBOR_JOBS_DIR"
export TRAJGEN_JOB_NAME="$JOB_NAME"
export TRAJGEN_JOB_NAME_PREFIX="$JOB_NAME_PREFIX"
export TRAJGEN_JOB_DIR="$JOB_DIR"
export TRAJGEN_TRAJECTORY_FILE_PATTERN="$JOB_DIR/<task-id>/agent/litellm-trajectory.jsonl"
export TRAJGEN_RUNTIME_ROOT="$(cfg runtime_info.input.runtime_mount.container_runtime_root)"
if [[ -z "$TRAJGEN_RUNTIME_ROOT" ]]; then
  case "$AGENT_NAME" in
    custom-claude-code)   export TRAJGEN_RUNTIME_ROOT="/opt/custom-agent-runtime/claude-code" ;;
    custom-opencode)      export TRAJGEN_RUNTIME_ROOT="/opt/custom-agent-runtime/opencode" ;;
    custom-openhands-sdk) export TRAJGEN_RUNTIME_ROOT="/opt/custom-agent-runtime/oh-sdk" ;;
  esac
fi
export TRAJGEN_RUNTIME_SOURCE_IMAGE="$(cfg runtime_info.input.agent.runtime_image)"
RUNTIME_HOST_PATH_RAW="$(cfg runtime_info.input.agent.runtime_host_path)"
if [[ -n "$RUNTIME_HOST_PATH_RAW" ]]; then
  export TRAJGEN_RUNTIME_HOST_PATH="$(abspath "$RUNTIME_HOST_PATH_RAW")"
else
  export TRAJGEN_RUNTIME_HOST_PATH=""
fi
export TRAJGEN_RUNTIME_IMAGE_SUBPATH="$(cfg runtime_info.input.runtime_mount.image_subpath)"
if [[ -z "$TRAJGEN_RUNTIME_IMAGE_SUBPATH" ]]; then
  TRAJGEN_RUNTIME_IMAGE_SUBPATH="${TRAJGEN_RUNTIME_ROOT#/}"
  export TRAJGEN_RUNTIME_IMAGE_SUBPATH
fi
export TRAJGEN_CUSTOM_AGENT_RUNTIME_ROOT="$TRAJGEN_RUNTIME_ROOT"
export TRAJGEN_CUSTOM_AGENT_RUNTIME_ENV_SCRIPT="$TRAJGEN_RUNTIME_ROOT/runtime-env.sh"
# Each custom scaffold's Harbor runner falls back to <root>/bin/<exe> when its
# own executable env var is unset, so leaving this unset is not fatal — but
# set it explicitly per scaffold anyway (matching blocks/evaluator/scripts/
# start.sh) so the executable is named rather than relying on that fallback.
case "$AGENT_NAME" in
  custom-openhands-sdk)
    export TRAJGEN_CUSTOM_AGENT_PYTHON="$TRAJGEN_RUNTIME_ROOT/bin/python"
    CUSTOM_AGENT_EXECUTABLE_AE='--ae CUSTOM_AGENT_PYTHON=$TRAJGEN_CUSTOM_AGENT_PYTHON'
    ;;
  custom-opencode)
    export TRAJGEN_CUSTOM_AGENT_OPENCODE="$TRAJGEN_RUNTIME_ROOT/bin/opencode"
    CUSTOM_AGENT_EXECUTABLE_AE='--ae CUSTOM_AGENT_OPENCODE=$TRAJGEN_CUSTOM_AGENT_OPENCODE'
    ;;
  *)
    export TRAJGEN_CUSTOM_AGENT_CLAUDE="$TRAJGEN_RUNTIME_ROOT/bin/claude"
    CUSTOM_AGENT_EXECUTABLE_AE='--ae CUSTOM_AGENT_CLAUDE=$TRAJGEN_CUSTOM_AGENT_CLAUDE'
    ;;
esac
export TRAJGEN_LITELLM_ANTHROPIC_BASE_URL="http://$(hostname -I | awk '{print $1}'):${LITELLM_PORT:-$(cfg runtime_info.input.litellm_proxy.port)}"
export TRAJGEN_LITELLM_MASTER_KEY="$(cfg runtime_info.input.litellm_proxy.master_key)"
if [[ -n "$LITELLM_UV_RAW" ]]; then
  LITELLM_UV="$(abspath "$LITELLM_UV_RAW")"
  export TRAJGEN_LITELLM_PYTHON="$LITELLM_UV/bin/python"
  export TRAJGEN_LITELLM_BIN="$LITELLM_UV/bin/litellm"
else
  export TRAJGEN_LITELLM_PYTHON="python"
  export TRAJGEN_LITELLM_BIN="litellm"
fi
while IFS= read -r env_line; do
  [[ -n "$env_line" ]] && export "$env_line"
done < <(export_run_environment)

if [[ -z "$RUN_COMMAND" ]]; then
  [[ -n "$TRAJGEN_AGENT_IMPORT_PATH" ]] || { echo "ERROR: runtime_info.input.agent.import_path is empty and no default is defined for agent.name=$(cfg runtime_info.input.agent.name)" >&2; exit 1; }
  EXTRA_ARGS=""
  if [[ -n "$N_TASKS" ]]; then
    EXTRA_ARGS=" --n-tasks $(printf '%q' "$N_TASKS")"
  fi
  # Never retry timed-out tasks — they'll just time out again and waste budget
  EXTRA_ARGS="$EXTRA_ARGS --retry-exclude AgentTimeoutError"
  # Exclusions resolve from the ledger, which records both what this block
  # consumed and what a human retired. A smoke re-runs its fixtures on purpose,
  # so it opts out of the ledger-derived ids via HARBOR_LEDGER_EXCLUDE=0.
  _LEDGER_FILE="$BLOCK_DIR/artifacts/processed_tasks.yaml"
  _EXCLUDE_IDS="$(python3 "$BLOCK_DIR/scripts/resolve_exclude_tasks.py" \
    --block-dir "$BLOCK_DIR" \
    --spec "${HARBOR_EXCLUDE_TASKS:-}" \
    --ledger "$_LEDGER_FILE" \
    --ledger-exclude "${HARBOR_LEDGER_EXCLUDE:-1}" 2>/dev/null || true)"
  if [[ -n "$_EXCLUDE_IDS" ]]; then
    echo "[start] excluding $(wc -w <<<"$_EXCLUDE_IDS") task(s) resolved from HARBOR_EXCLUDE_TASKS + artifacts/processed_tasks.yaml"
  fi
  for _excl_task in $_EXCLUDE_IDS; do
    EXTRA_ARGS="$EXTRA_ARGS --exclude-task-name $(printf '%q' "$_excl_task")"
  done
  # Each custom scaffold reads its LLM connection from different env var
  # names (checked against repos/harbor/src/harbor/agents/custom/*.py):
  #   custom-claude-code    -> ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / ANTHROPIC_MODEL
  #   custom-openhands-sdk  -> LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
  #   custom-opencode       -> provider-prefix-dependent; our models use the
  #                            openai/ prefix, which opencode maps to
  #                            OPENAI_BASE_URL / OPENAI_API_KEY
  # NOTE: single $ (not \$) — this value is spliced into RUN_COMMAND via
  # variable expansion, which does not re-interpret backslash escapes; the
  # backslash only belongs when the flags are written as literal text
  # directly inside the double-quoted RUN_COMMAND="..." string below.
  #
  # opencode needs --model as "provider/model"; others want the bare alias.
  # turn-limit kwarg name also differs: max_turns (claude-code), max_iterations
  # (openhands-sdk), none for opencode.
  case "$AGENT_NAME" in
    custom-openhands-sdk)
      AGENT_LLM_AE='--ae LLM_BASE_URL=$TRAJGEN_LITELLM_ANTHROPIC_BASE_URL --ae LLM_API_KEY=$TRAJGEN_LITELLM_MASTER_KEY --ae LLM_MODEL=$TRAJGEN_AGENT_MODEL_NAME_FULL'
      HARBOR_MODEL_ARG="$AGENT_MODEL_NAME"
      AGENT_TURNS_AK="max_iterations"
      ;;
    custom-opencode)
      AGENT_LLM_AE='--ae OPENAI_BASE_URL=$TRAJGEN_LITELLM_ANTHROPIC_BASE_URL --ae OPENAI_API_KEY=$TRAJGEN_LITELLM_MASTER_KEY'
      HARBOR_MODEL_ARG="$AGENT_MODEL_FULL"
      AGENT_TURNS_AK=""
      ;;
    *)
      AGENT_LLM_AE='--ae ANTHROPIC_BASE_URL=$TRAJGEN_LITELLM_ANTHROPIC_BASE_URL --ae ANTHROPIC_API_KEY=$TRAJGEN_LITELLM_MASTER_KEY --ae ANTHROPIC_MODEL=$TRAJGEN_AGENT_MODEL_NAME'
      HARBOR_MODEL_ARG="$AGENT_MODEL_NAME"
      AGENT_TURNS_AK="max_turns"
      ;;
  esac
  TURNS_AK_ARG=""
  if [[ -n "$AGENT_TURNS_AK" ]]; then
    TURNS_AK_ARG=" --ak ${AGENT_TURNS_AK}=$(printf '%q' "$MAX_TURNS")"
  else
    echo "[start] NOTE: agent.max_turns is not supported by $AGENT_NAME's Harbor agent; the configured value ($MAX_TURNS) will not be applied." >&2
  fi
  RUN_COMMAND="uv run harbor run --path $(printf '%q' "$HARBOR_DATASET_PATH") --jobs-dir $(printf '%q' "$HARBOR_JOBS_DIR") --agent-import-path $(printf '%q' "$TRAJGEN_AGENT_IMPORT_PATH") --job-name $(printf '%q' "$TRAJGEN_JOB_NAME") --mounts-json \"\$($(printf '%q' "$HARBOR_PYTHON") - <<'PY'
import json
import os
host_path = os.environ.get('TRAJGEN_RUNTIME_HOST_PATH', '')
if host_path:
    mounts = [{
        'type': 'bind',
        'source': host_path,
        'target': os.environ['TRAJGEN_RUNTIME_ROOT'],
        'read_only': True,
    }]
else:
    mounts = [{
        'type': 'image',
        'source': os.environ['TRAJGEN_RUNTIME_SOURCE_IMAGE'],
        'target': os.environ['TRAJGEN_RUNTIME_ROOT'],
        'read_only': True,
        'image': {'subpath': os.environ['TRAJGEN_RUNTIME_IMAGE_SUBPATH'].lstrip('/')},
    }]
print(json.dumps(mounts))
PY
)\" --model $(printf '%q' "$HARBOR_MODEL_ARG") --n-concurrent $(printf '%q' "$N_CONCURRENT")${EXTRA_ARGS} --timeout-multiplier $(printf '%q' "$TIMEOUT_MULTIPLIER") --max-retries $(printf '%q' "$MAX_RETRIES") --ak version=$(printf '%q' "$AGENT_VERSION")${TURNS_AK_ARG} --ak temperature=$(printf '%q' "$TEMPERATURE") $AGENT_LLM_AE --ae CUSTOM_AGENT_RUNTIME_ROOT=\$TRAJGEN_CUSTOM_AGENT_RUNTIME_ROOT $CUSTOM_AGENT_EXECUTABLE_AE --ae CUSTOM_AGENT_RUNTIME_ENV_SCRIPT=\$TRAJGEN_CUSTOM_AGENT_RUNTIME_ENV_SCRIPT"
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
LITELLM_CONFIG_NAME="${LITELLM_CONFIG_NAME}_tracer"
LITELLM_ARTIFACT_DIR="$BLOCK_DIR/artifacts/litellm/$JOB_NAME"
mkdir -p "$LITELLM_ARTIFACT_DIR"
LITELLM_CONFIG_PATH="$LITELLM_ARTIFACT_DIR/${LITELLM_CONFIG_NAME}.yaml"
TRAJECTORY_LOGGER_SOURCE="$HARBOR_DIR/scripts/serve_llm/trajectory_logger.py"
[[ -f "$TRAJECTORY_LOGGER_SOURCE" ]] || { echo "ERROR: trajectory_logger.py not found in Harbor checkout" >&2; exit 1; }
cp "$TRAJECTORY_LOGGER_SOURCE" "$LITELLM_ARTIFACT_DIR/trajectory_logger.py"
write_litellm_config "$LITELLM_CONFIG_PATH" "$LITELLM_TEMPLATE_PATH"
export TRAJGEN_LITELLM_CONFIG="$LITELLM_CONFIG_PATH"

if [[ "$PRINT_COMMAND_ONLY" == "1" ]]; then
  echo "=== tracer generated command ==="
  echo "LiteLLM config: $LITELLM_CONFIG_PATH"
  echo "$RUN_COMMAND"
  exit 0
fi

echo "=== starting LiteLLM proxy ===" | tee "$LOG_FILE"
# Harbor's serve_litellm.sh runs under `set -u` and only auto-defines
# LITELLM_STICKY_ROUTING_ALIASES when CONFIG_NAME == "litellm_config". Our
# generated CONFIG_NAME is "litellm_config_tracer", so the var is left
# unset and a later `[[ -n "$LITELLM_STICKY_ROUTING_ALIASES" ]]` reference
# trips the unbound-variable trap. Define an empty default here.
setsid bash -c '
  cd "$1"
  PATH="$(dirname "$TRAJGEN_LITELLM_BIN"):$PATH" \
    LITELLM_CONFIG="$2" \
    LITELLM_LOG_FOLDER="$3/logs" \
    LITELLM_PORT="$4" \
    API_KEY="$5" \
    LITELLM_STICKY_ROUTING_ALIASES="${LITELLM_STICKY_ROUTING_ALIASES:-}" \
    bash scripts/serve_llm/serve_litellm.sh
' bash "$HARBOR_DIR" "$LITELLM_CONFIG_PATH" "$LITELLM_ARTIFACT_DIR" "${LITELLM_PORT:-$(cfg runtime_info.input.litellm_proxy.port)}" "$TRAJGEN_LITELLM_MASTER_KEY" >>"$LOG_FILE" 2>&1 &
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

for _ in {1..30}; do
  if "$TRAJGEN_LITELLM_PYTHON" - "$TRAJGEN_LITELLM_ANTHROPIC_BASE_URL" <<'PY' >/dev/null 2>&1
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
    break
  fi
  if ! kill -0 "$LITELLM_PID" >/dev/null 2>&1; then
    echo "ERROR: LiteLLM proxy exited before Harbor job started" | tee -a "$LOG_FILE"
    exit 1
  fi
  sleep 1
done

echo "=== tracer start ===" | tee -a "$LOG_FILE"
echo "Harbor dir: $HARBOR_PATH_RAW" | tee -a "$LOG_FILE"
echo "Producer:   $PRODUCER_BLOCK" | tee -a "$LOG_FILE"
echo "Format:     $OUTPUT_FORMAT" | tee -a "$LOG_FILE"
echo "Dataset:    $HARBOR_DATASET_PATH_RAW" | tee -a "$LOG_FILE"
echo "Jobs dir:   $HARBOR_JOBS_DIR_RAW" | tee -a "$LOG_FILE"
echo "Job prefix: $JOB_NAME_PREFIX" | tee -a "$LOG_FILE"
echo "Job dir:    $JOB_DIR_RAW" | tee -a "$LOG_FILE"
echo "Traj files: $TRAJGEN_TRAJECTORY_FILE_PATTERN" | tee -a "$LOG_FILE"
echo "Config:     $JOB_DIR_RAW/config.yaml" | tee -a "$LOG_FILE"
echo "Command:    $RUN_COMMAND" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

(cd "$HARBOR_DIR" && bash -lc "$RUN_COMMAND") 2>&1 | tee -a "$LOG_FILE"

SFT_CONVERT_ENABLED="$(cfg runtime_info.input.sft_conversion.enabled)"
if [[ "$SFT_CONVERT_ENABLED" == "true" ]]; then
  echo "" | tee -a "$LOG_FILE"
  echo "=== tracer: post-run SFT conversion ===" | tee -a "$LOG_FILE"
  bash "$BLOCK_DIR/scripts/convert_trajectories.sh" --job "$JOB_NAME" 2>&1 | tee -a "$LOG_FILE"
fi

echo "" | tee -a "$LOG_FILE"
echo "tracer complete. Expected job dir: $JOB_DIR_RAW" | tee -a "$LOG_FILE"
echo "Expected trajectory files: $TRAJGEN_TRAJECTORY_FILE_PATTERN" | tee -a "$LOG_FILE"
