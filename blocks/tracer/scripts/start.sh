#!/usr/bin/env bash
# Run the configured Harbor trajectory generation command.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${TRAJGEN_CONFIG:-$BLOCK_DIR/config.yaml}"
[[ "$CONFIG" = /* ]] || CONFIG="$BLOCK_DIR/$CONFIG"
export TRAJGEN_CONFIG="$CONFIG"
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
runtime_info.input.harbor_job.samples_per_task >= 2 runs a best-of-n campaign:
k sequential sample jobs (named <job>-s<i>), each with its own proxy and
conversion. runtime_info.input.agent.scaffolds runs a multi-scaffold campaign:
one sequential job per scaffold. Set both for m scaffolds x k samples. Every
job of a campaign runs the same batch: the exclusion set is resolved once, at
launch, and reused for each job.
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
import os
from pathlib import Path
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required", file=sys.stderr)
    sys.exit(2)

config_path, output_path, template_path = sys.argv[1:4]
def read_yaml(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return yaml.safe_load(stream) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        print(f"ERROR: invalid YAML in {path}{location}", file=sys.stderr)
        raise SystemExit(2)

config = read_yaml(config_path)

model_api = dict((config.get("runtime_info") or {}).get("input", {}).get("llm_api") or {})
proxy = (config.get("runtime_info") or {}).get("input", {}).get("litellm_proxy") or {}

env_base = os.getenv("OPENAI_API_BASE_URL") or os.getenv("OPENAI_BASE_URL")
env_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")
env_model = os.getenv("OPENAI_MODEL") or os.getenv("ANTHROPIC_MODEL")
if env_base:
    model_api["api_base_url"] = env_base
    model_api["api_base"] = env_base
if env_key:
    model_api["api_key"] = env_key
if env_model:
    normalized_model = env_model if "/" in env_model else f"openai/{env_model}"
    model_api["model"] = normalized_model
    model_api["upstream_model"] = normalized_model

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
    loaded = read_yaml(template_file)
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

fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.chmod(output_path, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
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

# Multi-scaffold campaign knob (runtime_info.input.agent.scaffolds; default
# null = single-agent mode and everything below is untouched). Detected and
# validated BEFORE any side effect so an invalid campaign fails loud: an
# unknown scaffold name would otherwise fall into the claude-code default of
# every per-scaffold case dispatch and launch the wrong agent.
SCAFFOLDS_RAW="$(cfg runtime_info.input.agent.scaffolds)"
CAMPAIGN=0
if [[ -n "$SCAFFOLDS_RAW" && "$SCAFFOLDS_RAW" != "[]" ]]; then
  CAMPAIGN=1
  # Validates names/fields/duplicates and prints the campaign banner.
  python3 "$BLOCK_DIR/scripts/validate_scaffolds.py" --config "$CONFIG"
  # One explicit command or job-name prefix would collide across the sequential
  # scaffold jobs, which derive distinct names from agent name+version.
  RUN_COMMAND_CHECK="$(cfg command_override)"
  [[ -z "$RUN_COMMAND_CHECK" ]] || { echo "ERROR: command_override is set — not supported in multi-scaffold campaign mode (one explicit job command cannot serve N scaffolds)" >&2; exit 1; }
  JOB_NAME_PREFIX_CHECK="$(cfg runtime_info.input.harbor_job.job_name_prefix)"
  [[ -z "$JOB_NAME_PREFIX_CHECK" ]] || { echo "ERROR: runtime_info.input.harbor_job.job_name_prefix is set — not supported in multi-scaffold campaign mode (scaffold jobs derive distinct names from agent name+version)" >&2; exit 1; }
  JOB_DIR_CHECK="$(cfg job_dir)"
  [[ -z "$JOB_DIR_CHECK" ]] || { echo "ERROR: job_dir is set — not supported in multi-scaffold campaign mode (all scaffold jobs would share one job dir)" >&2; exit 1; }
  # A global import path / api protocol would override the per-scaffold agent
  # dispatch for EVERY scaffold job — all N jobs would run the same agent
  # class under misleading per-scaffold job names.
  IMPORT_PATH_CHECK="$(cfg runtime_info.input.agent.import_path)"
  [[ -z "$IMPORT_PATH_CHECK" ]] || { echo "ERROR: runtime_info.input.agent.import_path is set — not supported in multi-scaffold campaign mode (each scaffold job derives its own import path from its agent name)" >&2; exit 1; }
  API_PROTOCOL_CHECK="$(cfg runtime_info.input.agent.api_protocol)"
  [[ -z "$API_PROTOCOL_CHECK" ]] || { echo "ERROR: runtime_info.input.agent.api_protocol is set — not supported in multi-scaffold campaign mode (scaffold-specific protocol defaults come from each agent name)" >&2; exit 1; }
fi

# Best-of-n knob (runtime_info.input.harbor_job.samples_per_task): absent key =
# default 1 (single pass, off). Anything present must be an integer >= 1; > 1
# runs k sequential sample jobs per scaffold job. Validated here, with the
# scaffolds knob, so a bad value fails before staging and preflight. The two
# knobs compose: m scaffolds x k samples = m*k sequential jobs.
SAMPLES_PER_TASK="$(cfg runtime_info.input.harbor_job.samples_per_task)"
if [[ -z "$SAMPLES_PER_TASK" ]]; then
  SAMPLES_PER_TASK=1
fi
if ! [[ "$SAMPLES_PER_TASK" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: runtime_info.input.harbor_job.samples_per_task must be an integer >= 1 (got: '$SAMPLES_PER_TASK')" >&2
  exit 1
fi
if [[ "$SAMPLES_PER_TASK" -gt 1 && -n "$(cfg command_override)" ]]; then
  echo "ERROR: samples_per_task > 1 (campaign mode) is incompatible with command_override; remove command_override so each sample gets its own job name" >&2
  exit 1
fi

if [[ "$UPDATE_REPOS" == "1" ]]; then
  bash "$BLOCK_DIR/scripts/update_repos.sh"
fi

# Stage by default. Preflight no longer fails on an unstaged block — an unstaged
# block is simply one that has not run yet — so a launch has to do the staging
# itself, or Harbor is pointed at a directory nobody filled. Staging is
# idempotent for the same selection. Capture its version path: a different
# prepare may advance the public pointer while Harbor has queued trials.
# Local sources remain task links; mutable external/SWE pools are not byte
# snapshots. Set TRAJGEN_PREPARE_TASKS=0 to use existing staging.
PREPARED_HARBOR_DATASET_PATH=""
if [[ "${TRAJGEN_PREPARE_TASKS:-1}" != "0" ]]; then
  PREPARE_RESULT="$(mktemp)"
  if bash "$BLOCK_DIR/scripts/prepare_tasks.sh" --config "$CONFIG" --result-file "$PREPARE_RESULT"; then
    if ! PREPARED_HARBOR_DATASET_PATH="$(python3 - "$PREPARE_RESULT" <<'PY'
import json
from pathlib import Path
import sys

data = json.loads(Path(sys.argv[1]).read_text())
print(Path(data["dataset_path"]).resolve(strict=True))
PY
)"; then
      rm -f -- "$PREPARE_RESULT"
      exit 1
    fi
  else
    prepare_status=$?
    rm -f -- "$PREPARE_RESULT"
    exit "$prepare_status"
  fi
  rm -f -- "$PREPARE_RESULT"
fi

echo "=== tracer preflight ==="
bash "$BLOCK_DIR/scripts/dryrun.sh"
echo ""

# One log file per start.sh invocation: a campaign's m*k jobs append to it.
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/tracer_$(date +%Y-%m-%d_%H-%M-%S).log"

# Resolve everything that depends on WHICH scaffold a job runs: job identity
# (name prefix, timestamped job name), the agent import path and API protocol,
# the runtime image / host path / executable, and the exported TRAJGEN_* values
# the Harbor command reads. Called once in single-agent mode (with the four
# runtime_info.input.agent identity fields) and once per entry in a
# multi-scaffold campaign. Runs in the CURRENT shell on purpose —
# prepare_job_sample, launch_job_sample, and the EXIT trap read the globals it
# sets, which a subshell would orphan.
#
# Args: agent_name agent_version runtime_image runtime_host_path
configure_job() {
  local job_agent_name="$1"
  local job_agent_version="$2"
  local job_runtime_image="$3"
  local job_runtime_host_path_raw="$4"

HARBOR_PATH_RAW="$(cfg meta_info.repositories.harbor.path)"
RUN_COMMAND="$(cfg command_override)"
COMMAND_OVERRIDE="$RUN_COMMAND"
UV_PROJECT_ENVIRONMENT_RAW="$(cfg meta_info.environment.harbor_uv)"
LITELLM_UV_RAW="$(cfg meta_info.environment.litellm_uv)"
HARBOR_DATASET_PATH_RAW="$(cfg runtime_info.input.harbor_job.dataset_path)"
HARBOR_JOBS_DIR_RAW="$(cfg runtime_info.input.harbor_job.jobs_dir)"
HARBOR_DATASET="$(cfg runtime_info.input.harbor_job.dataset)"
TASK_SOURCE_DATASET_NAME="$(python3 "$BLOCK_DIR/scripts/task_source.py" --config "$CONFIG" --field dataset_name)"

# Stratified mode stages curator's stratified sub-pool (same override
# prepare_tasks.sh applies), so the derived dataset, dataset path, and job
# name prefix below all describe the sub-pool the run actually stages.
TASK_SOURCE_POOL="$(cfg runtime_info.input.task_source.pool)"
case "$TASK_SOURCE_POOL" in
  merged|"") ;;
  stratified)
    # The sub-pool is a local curator directory; under provider: huggingface
    # prepare_tasks.sh would hand that path to snapshot_download as a repo id.
    TASK_SOURCE_PROVIDER="$(cfg runtime_info.input.task_source.provider)"
    [[ "$TASK_SOURCE_PROVIDER" == "local" ]] || { echo "ERROR: task_source.pool is stratified but task_source.provider is '$TASK_SOURCE_PROVIDER' — the stratified sub-pool is a local curator directory; set task_source.provider: local" >&2; exit 1; }
    TASK_SOURCE_STRATIFIED="$(cfg runtime_info.input.task_source.stratified_dataset_dir)"
    [[ -n "$TASK_SOURCE_STRATIFIED" ]] || { echo "ERROR: task_source.pool is stratified but task_source.stratified_dataset_dir is empty (set it in config.yaml; generate the pool: python3 ../curator/scripts/select_stratified_tasks.py)" >&2; exit 1; }
    TASK_SOURCE_DATASET_NAME="$TASK_SOURCE_STRATIFIED"
    ;;
  *)
    echo "ERROR: unsupported task_source.pool: $TASK_SOURCE_POOL (merged|stratified)" >&2
    exit 1
    ;;
esac
if [[ -z "$HARBOR_DATASET" && -n "$TASK_SOURCE_DATASET_NAME" ]]; then
  if [[ "$TASK_SOURCE_POOL" == "stratified" ]]; then
    HARBOR_DATASET="$(basename "$TASK_SOURCE_DATASET_NAME")"
  else
    HARBOR_DATASET="$(python3 "$BLOCK_DIR/scripts/task_source.py" --config "$CONFIG" --field dataset_id)"
  fi
fi
# The staging path follows the effective task source — the derivation
# prepare_tasks.sh uses — not harbor_job.dataset, which only names the job. An
# explicit dataset name would otherwise point the launch at a directory staging
# never wrote (or, in stratified mode, at an older staging of the merged pool).
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
AGENT_NAME="$job_agent_name"
AGENT_VERSION="$job_agent_version"
AGENT_MODEL_NAME="$(cfg runtime_info.input.agent.model_name)"
RUNTIME_MODEL="${OPENAI_MODEL:-${ANTHROPIC_MODEL:-}}"
if [[ -n "$RUNTIME_MODEL" ]]; then
  AGENT_MODEL_FULL="$RUNTIME_MODEL"
  [[ "$AGENT_MODEL_FULL" == */* ]] || AGENT_MODEL_FULL="openai/$AGENT_MODEL_FULL"
  AGENT_MODEL_NAME="${AGENT_MODEL_FULL##*/}"
elif [[ -z "$AGENT_MODEL_NAME" ]]; then
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
JOB_DIR_EXPLICIT="$JOB_DIR_RAW"
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
if [[ -z "$TRAJGEN_AGENT_API_PROTOCOL" && "$job_agent_name" == "custom-claude-code" ]]; then
  export TRAJGEN_AGENT_API_PROTOCOL="anthropic"
fi
export TRAJGEN_AGENT_MODEL_NAME="$AGENT_MODEL_NAME"
export TRAJGEN_AGENT_MODEL_NAME_FULL="$AGENT_MODEL_FULL"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/tracer_$(date +%Y-%m-%d_%H-%M-%S).log"

JOB_DIR="$(abspath "$JOB_DIR_RAW")"
if [[ -n "$PREPARED_HARBOR_DATASET_PATH" ]]; then
  HARBOR_DATASET_PATH="$PREPARED_HARBOR_DATASET_PATH"
else
  HARBOR_DATASET_PATH="$(python3 - "$(abspath "$HARBOR_DATASET_PATH_RAW")" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve(strict=True))
PY
)"
fi
HARBOR_JOBS_DIR="$(abspath "$HARBOR_JOBS_DIR_RAW")"
mkdir -p "$(dirname "$HARBOR_DATASET_PATH")" "$HARBOR_JOBS_DIR"

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
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export TRAJGEN_ARTIFACTS_DIR="$BLOCK_DIR/artifacts"
export TRAJGEN_HARBOR_DATASET_PATH="$HARBOR_DATASET_PATH"
export TRAJGEN_HARBOR_JOBS_DIR="$HARBOR_JOBS_DIR"
export TRAJGEN_JOB_NAME_PREFIX="$JOB_NAME_PREFIX"
export TRAJGEN_RUNTIME_ROOT="$(cfg runtime_info.input.runtime_mount.container_runtime_root)"
if [[ -z "$TRAJGEN_RUNTIME_ROOT" ]]; then
  case "$AGENT_NAME" in
    custom-claude-code)   export TRAJGEN_RUNTIME_ROOT="/opt/custom-agent-runtime/claude-code" ;;
    custom-opencode)      export TRAJGEN_RUNTIME_ROOT="/opt/custom-agent-runtime/opencode" ;;
    custom-openhands-sdk) export TRAJGEN_RUNTIME_ROOT="/opt/custom-agent-runtime/oh-sdk" ;;
  esac
fi
export TRAJGEN_RUNTIME_SOURCE_IMAGE="$job_runtime_image"
RUNTIME_HOST_PATH_RAW="$job_runtime_host_path_raw"
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
# harbor falls back to <root>/bin/<exe> if unset; set explicitly (matches blocks/evaluator)
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
# Primary LAN IP, portable: hostname -I is Linux-only; macOS uses ipconfig.
# Falls back to host.docker.internal (OrbStack / Docker Desktop both route it
# from containers to the host) when no interface IP can be resolved.
lan_ip() {
  if hostname -I >/dev/null 2>&1; then
    hostname -I | awk '{print $1}'
  elif command -v ipconfig >/dev/null 2>&1; then
    ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true
  fi
}
_lan_ip="$(lan_ip)"
[[ -n "$_lan_ip" ]] || _lan_ip="host.docker.internal"
export TRAJGEN_LITELLM_ANTHROPIC_BASE_URL="http://${_lan_ip}:${LITELLM_PORT:-$(cfg runtime_info.input.litellm_proxy.port)}"
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
TRAJECTORY_LOGGER_SOURCE="$HARBOR_DIR/scripts/serve_llm/trajectory_logger.py"
[[ -f "$TRAJECTORY_LOGGER_SOURCE" ]] || { echo "ERROR: trajectory_logger.py not found in Harbor checkout" >&2; exit 1; }
}

# Resolve the exclusion portion of the Harbor command into EXTRA_ARGS for one
# job. The exclusion set is resolved ONCE per start.sh invocation and reused for
# every job, so all m*k jobs of a campaign run the identical batch. Nothing on
# the launch path writes the ledger (bookkeeping happens after the run), so
# prior runs' exclusions stay in force for every sample and scaffold — a
# campaign does not re-run tasks an earlier run already processed. The snapshot
# also keeps the batch stable if the ledger is edited while a campaign runs.
EXCLUDE_IDS_RESOLVED=0
EXCLUDE_IDS=""
resolve_job_exclude_args() {
  local exclude_task
  EXTRA_ARGS=""
  if [[ -n "$N_TASKS" ]]; then
    EXTRA_ARGS=" --n-tasks $(printf '%q' "$N_TASKS")"
  fi
  # Never retry timed-out tasks — they'll just time out again and waste budget
  EXTRA_ARGS="$EXTRA_ARGS --retry-exclude AgentTimeoutError"
  # Exclusions resolve from the ledger, which records both what this block
  # consumed and what a human retired. A smoke re-runs its fixtures on purpose,
  # so it opts out of the ledger-derived ids via HARBOR_LEDGER_EXCLUDE=0 — the
  # same opt-out re-runs already-processed tasks in a campaign.
  if [[ "$EXCLUDE_IDS_RESOLVED" != "1" ]]; then
    EXCLUDE_IDS="$(python3 "$BLOCK_DIR/scripts/resolve_exclude_tasks.py" \
      --block-dir "$BLOCK_DIR" \
      --spec "${HARBOR_EXCLUDE_TASKS:-}" \
      --ledger "$BLOCK_DIR/artifacts/processed_tasks.yaml" \
      --ledger-exclude "${HARBOR_LEDGER_EXCLUDE:-1}" 2>/dev/null || true)"
    EXCLUDE_IDS_RESOLVED=1
    if [[ -n "$EXCLUDE_IDS" ]]; then
      echo "[start] excluding $(wc -w <<<"$EXCLUDE_IDS") task(s) resolved from HARBOR_EXCLUDE_TASKS + artifacts/processed_tasks.yaml"
    fi
  fi
  for exclude_task in $EXCLUDE_IDS; do
    EXTRA_ARGS="$EXTRA_ARGS --exclude-task-name $(printf '%q' "$exclude_task")"
  done
}

# One sample of a rollout: job naming/dirs, per-job config snapshot, exclusion
# resolution, Harbor command build, and the per-job LiteLLM config render.
# Invoked once for the default single pass (index 1 of 1, unsuffixed job name —
# byte-identical to the pre-campaign behavior) and once per sample in campaign
# mode (samples_per_task > 1), where each sample gets a distinct -s<i> suffix
# so k job dirs and k LiteLLM configs stay separate. Always follows a
# configure_job call, which sets the scaffold this sample runs.
prepare_job_sample() {
  local sample_index="$1"
  local sample_total="$2"
  SAMPLE_JOB_NAME="$JOB_NAME"
  SAMPLE_JOB_DIR_RAW="$JOB_DIR_RAW"
  if [[ "$sample_total" -gt 1 ]]; then
    SAMPLE_JOB_NAME="${JOB_NAME}-s${sample_index}"
    if [[ -n "$JOB_DIR_EXPLICIT" ]]; then
      SAMPLE_JOB_DIR_RAW="${JOB_DIR_RAW}-s${sample_index}"
    else
      SAMPLE_JOB_DIR_RAW="${HARBOR_JOBS_DIR_RAW%/}/$SAMPLE_JOB_NAME"
    fi
  fi
  SAMPLE_JOB_DIR="$(abspath "$SAMPLE_JOB_DIR_RAW")"
  mkdir -p "$SAMPLE_JOB_DIR"
  python3 "$BLOCK_DIR/../../scripts/redact_archive_config.py" "$CONFIG" "$SAMPLE_JOB_DIR/config.yaml"
  export TRAJGEN_JOB_NAME="$SAMPLE_JOB_NAME"
  export TRAJGEN_JOB_DIR="$SAMPLE_JOB_DIR"
  export TRAJGEN_TRAJECTORY_FILE_PATTERN="$SAMPLE_JOB_DIR/<task-id>/agent/litellm-trajectory.jsonl"

  RUN_COMMAND="$COMMAND_OVERRIDE"
  if [[ -z "$RUN_COMMAND" ]]; then
    [[ -n "$TRAJGEN_AGENT_IMPORT_PATH" ]] || { echo "ERROR: runtime_info.input.agent.import_path is empty and no default is defined for agent.name=$AGENT_NAME" >&2; exit 1; }
    resolve_job_exclude_args
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

  LITELLM_ARTIFACT_DIR="$BLOCK_DIR/artifacts/litellm/$SAMPLE_JOB_NAME"
  mkdir -p "$LITELLM_ARTIFACT_DIR"
  LITELLM_CONFIG_PATH="$LITELLM_ARTIFACT_DIR/${LITELLM_CONFIG_NAME}.yaml"
  cp "$TRAJECTORY_LOGGER_SOURCE" "$LITELLM_ARTIFACT_DIR/trajectory_logger.py"
  write_litellm_config "$LITELLM_CONFIG_PATH" "$LITELLM_TEMPLATE_PATH"
  export TRAJGEN_LITELLM_CONFIG="$LITELLM_CONFIG_PATH"
}

SFT_CONVERT_ENABLED="$(cfg runtime_info.input.sft_conversion.enabled)"
SFT_OUT_DIR="$(cfg runtime_info.input.sft_conversion.out_dir)"
[[ -n "$SFT_OUT_DIR" ]] || SFT_OUT_DIR="artifacts/sft_data"

# The EXIT trap reads LITELLM_PID at fire time, so it always kills the proxy of
# the job in flight (and no-ops between jobs once stop_litellm_proxy has reaped
# one) — a campaign aborting mid-job can neither double-kill nor leak.
LITELLM_PID=""
cleanup_litellm() {
  if [[ -n "$LITELLM_PID" ]] && kill -0 "$LITELLM_PID" >/dev/null 2>&1; then
    kill -- "-$LITELLM_PID" >/dev/null 2>&1 || kill "$LITELLM_PID" >/dev/null 2>&1 || true
    wait "$LITELLM_PID" >/dev/null 2>&1 || true
  fi
}
stop_litellm_proxy() {
  cleanup_litellm || true
  LITELLM_PID=""
  # A leaked listener on the proxy port blocks the NEXT job's proxy —
  # serve_litellm.sh exits with "Port already in use" and the agent then hangs
  # against a dead endpoint until its task timeout. litellm can escape the
  # process group killed above, so sweep the port as a fallback.
  local port="${LITELLM_PORT:-$(cfg runtime_info.input.litellm_proxy.port)}"
  if command -v lsof >/dev/null 2>&1; then
    local leaked
    leaked="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$leaked" ]]; then
      echo "[start] killing leaked LiteLLM listener(s) on port $port: $(echo $leaked | tr '\n' ' ')" | tee -a "$LOG_FILE" >&2
      kill $leaked 2>/dev/null || true
      local _wait
      for _wait in {1..10}; do
        leaked="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
        [[ -z "$leaked" ]] && break
        sleep 1
      done
      [[ -z "$leaked" ]] || kill -9 $leaked 2>/dev/null || true
    fi
  fi
}
_archive_run_on_exit() {
    local rc=$?
    cleanup_litellm || true
    bash "$BLOCK_DIR/scripts/archive_run.sh" "$rc" "$RUN_STARTED_AT" || true
    exit $rc
}

# One full per-job lifecycle after prepare_job_sample: start the per-job
# LiteLLM proxy, wait for readiness, run Harbor, stop the proxy, convert the
# job. Jobs run strictly sequentially — across samples AND scaffolds — because
# every job reuses the same proxy port and the readiness probe accepts any
# listener on it, so two live proxies would cross-write trajectories.
#
# Args: convert_scaffold ("auto" keeps convert_trajectories.sh's config-derived
# scaffold, i.e. single-agent behavior; a campaign job passes its own key).
launch_job_sample() {
  local convert_scaffold="$1"
  echo "=== starting LiteLLM proxy ===" | tee -a "$LOG_FILE"
  # Harbor's serve_litellm.sh runs under `set -u` and only auto-defines
  # LITELLM_STICKY_ROUTING_ALIASES when CONFIG_NAME == "litellm_config". Our
  # generated CONFIG_NAME is "litellm_config_tracer", so the var is left
  # unset and a later `[[ -n "$LITELLM_STICKY_ROUTING_ALIASES" ]]` reference
  # trips the unbound-variable trap. Define an empty default here.
  # setsid is Linux-only; emulate it with python3 os.setsid + execvp on macOS.
  _setsid_cmd=(setsid)
  if ! command -v setsid >/dev/null 2>&1; then
    _setsid_cmd=(python3 -c 'import os,sys;os.setsid();os.execvp(sys.argv[1],sys.argv[1:])')
  fi
  "${_setsid_cmd[@]}" bash -c '
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
  # The loop above falls through silently after 30 attempts. A proxy that is
  # alive but never listening (e.g. port held by a leaked instance) means the
  # agent would hang against a dead endpoint for its whole task timeout — fail
  # the launch fast instead.
  if ! "$TRAJGEN_LITELLM_PYTHON" - "$TRAJGEN_LITELLM_ANTHROPIC_BASE_URL" <<'PY' >/dev/null 2>&1
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
    stop_litellm_proxy
    echo "ERROR: LiteLLM proxy did not become ready on $TRAJGEN_LITELLM_ANTHROPIC_BASE_URL within 30s — is the port held by a leaked proxy? (this listener was killed; re-run the job)" | tee -a "$LOG_FILE"
    exit 1
  fi

  echo "=== tracer start ===" | tee -a "$LOG_FILE"
  echo "Harbor dir: $HARBOR_PATH_RAW" | tee -a "$LOG_FILE"
  echo "Producer:   $PRODUCER_BLOCK" | tee -a "$LOG_FILE"
  echo "Format:     $OUTPUT_FORMAT" | tee -a "$LOG_FILE"
  echo "Dataset:    $HARBOR_DATASET_PATH_RAW" | tee -a "$LOG_FILE"
  echo "Jobs dir:   $HARBOR_JOBS_DIR_RAW" | tee -a "$LOG_FILE"
  echo "Job prefix: $JOB_NAME_PREFIX" | tee -a "$LOG_FILE"
  echo "Job dir:    $SAMPLE_JOB_DIR_RAW" | tee -a "$LOG_FILE"
  echo "Traj files: $TRAJGEN_TRAJECTORY_FILE_PATTERN" | tee -a "$LOG_FILE"
  echo "Config:     $SAMPLE_JOB_DIR_RAW/config.yaml" | tee -a "$LOG_FILE"
  echo "Command:    $RUN_COMMAND" | tee -a "$LOG_FILE"
  if [[ "$(python3 "$BLOCK_DIR/scripts/task_source.py" --config "$CONFIG" --field producer)" == terminal-curator ]]; then
    python3 "$BLOCK_DIR/../terminal-curator/scripts/publication.py" --staging "$HARBOR_DATASET_PATH"
  fi
  echo "" | tee -a "$LOG_FILE"

  (cd "$HARBOR_DIR" && bash -lc "$RUN_COMMAND") 2>&1 | tee -a "$LOG_FILE"

  # Stop this job's proxy before converting and before the next job starts:
  # the next proxy reuses the same port, and a lingering listener would be
  # mistaken for the new proxy by the readiness probe above.
  stop_litellm_proxy

  if [[ "$SFT_CONVERT_ENABLED" == "true" ]]; then
    echo "" | tee -a "$LOG_FILE"
    echo "=== tracer: post-run SFT conversion ===" | tee -a "$LOG_FILE"
    if [[ "$convert_scaffold" == "auto" ]]; then
      bash "$BLOCK_DIR/scripts/convert_trajectories.sh" --job "$SAMPLE_JOB_NAME" 2>&1 | tee -a "$LOG_FILE"
    else
      # Campaign jobs ran a scaffold other than agent.name; auto-derivation
      # would read the wrong scaffold from config, so pass the key explicitly.
      bash "$BLOCK_DIR/scripts/convert_trajectories.sh" --job "$SAMPLE_JOB_NAME" --scaffold "$convert_scaffold" 2>&1 | tee -a "$LOG_FILE"
    fi
  fi
}

# Every job for ONE scaffold: configure it, then run samples_per_task sample
# jobs (1 = a single unsuffixed job). Leaves the job names it ran, comma-joined,
# in SCAFFOLD_JOBS. With --dry-run-command it prints the first sample's command
# and launches nothing.
#
# Args: agent_name agent_version runtime_image runtime_host_path convert_scaffold
SCAFFOLD_JOBS=""
run_scaffold_jobs() {
  local convert_scaffold="$5"
  local sample_index
  configure_job "$1" "$2" "$3" "$4"
  SCAFFOLD_JOBS=""
  if [[ "$PRINT_COMMAND_ONLY" == "1" ]]; then
    prepare_job_sample 1 "$SAMPLES_PER_TASK"
    if [[ "$SAMPLES_PER_TASK" -gt 1 ]]; then
      echo "=== tracer campaign: $SAMPLES_PER_TASK samples (expected jobs ${JOB_NAME}-s1 .. ${JOB_NAME}-s${SAMPLES_PER_TASK}) ==="
    fi
    echo "=== tracer generated command ==="
    echo "LiteLLM config: $LITELLM_CONFIG_PATH"
    echo "$RUN_COMMAND"
    return 0
  fi
  if [[ "$SAMPLES_PER_TASK" -gt 1 ]]; then
    echo "Job names:  ${JOB_NAME}-s1 .. ${JOB_NAME}-s${SAMPLES_PER_TASK}" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
  fi
  for sample_index in $(seq 1 "$SAMPLES_PER_TASK"); do
    prepare_job_sample "$sample_index" "$SAMPLES_PER_TASK"
    launch_job_sample "$convert_scaffold"
    SCAFFOLD_JOBS="${SCAFFOLD_JOBS:+${SCAFFOLD_JOBS},}${SAMPLE_JOB_NAME}"
    if [[ "$SAMPLES_PER_TASK" -gt 1 ]]; then
      echo "[campaign] sample ${sample_index}/${SAMPLES_PER_TASK} complete: job ${SAMPLE_JOB_NAME} -> ${SAMPLE_JOB_DIR_RAW}" | tee -a "$LOG_FILE"
    fi
  done
}

if [[ "$PRINT_COMMAND_ONLY" != "1" ]]; then
  trap _archive_run_on_exit EXIT
  : >"$LOG_FILE"
  if [[ "$SAMPLES_PER_TASK" -gt 1 ]]; then
    echo "=== tracer campaign: $SAMPLES_PER_TASK samples per staged task (best-of-n) ===" | tee -a "$LOG_FILE"
  fi
  if [[ "$CAMPAIGN" == "1" || "$SAMPLES_PER_TASK" -gt 1 ]]; then
    echo "Exclusions: resolved once at launch and reused for every job, so each job runs the same batch (tasks processed by earlier runs stay excluded; HARBOR_LEDGER_EXCLUDE=0 re-runs them)" | tee -a "$LOG_FILE"
  fi
fi

# The post-run hints name --sft-root / --out-dir only when sft_conversion.out_dir
# is not the default the selection and merge scripts already assume.
SFT_ROOT_FLAG=""
if [[ "$SFT_OUT_DIR" != "artifacts/sft_data" ]]; then
  SFT_ROOT_FLAG=" --sft-root $SFT_OUT_DIR"
fi

if [[ "$CAMPAIGN" == "1" ]]; then
  # Sequential on purpose (see launch_job_sample). With samples_per_task > 1
  # each scaffold runs its k samples before the next scaffold starts.
  CAMPAIGN_JOBS=""
  CAMPAIGN_SELECT_HINTS=""
  CAMPAIGN_SELECTED=""
  # Read the scaffold list on fd 3 so nothing inside a job (Harbor's bash -lc
  # in particular) can consume the loop's stdin and skip the next scaffold.
  while IFS=$'\t' read -r -u 3 sf_name sf_version sf_image sf_host; do
    [[ -n "$sf_name" ]] || continue
    case "$sf_name" in
      custom-claude-code)   sf_key="claude_code" ;;
      custom-opencode)      sf_key="open_code" ;;
      custom-openhands-sdk) sf_key="openhands_sdk" ;;
    esac
    run_scaffold_jobs "$sf_name" "$sf_version" "$sf_image" "$sf_host" "$sf_key"
    [[ "$PRINT_COMMAND_ONLY" != "1" ]] || continue
    CAMPAIGN_JOBS="${CAMPAIGN_JOBS:+${CAMPAIGN_JOBS},}${SCAFFOLD_JOBS}"
    if [[ "$SAMPLES_PER_TASK" -gt 1 ]]; then
      # Best-of-n runs WITHIN a scaffold (its samples are comparable, so
      # behavioral dedup applies); the per-scaffold selections are then merged
      # ACROSS scaffolds, where trajectories differ by design and are all kept.
      CAMPAIGN_SELECT_HINTS="${CAMPAIGN_SELECT_HINTS}  python3 scripts/select_trajectories.py --jobs ${SCAFFOLD_JOBS} --per-task-cap 2${SFT_ROOT_FLAG} --out-dir ${SFT_OUT_DIR%/}/selected-${sf_name}"$'\n'
      CAMPAIGN_SELECTED="${CAMPAIGN_SELECTED:+${CAMPAIGN_SELECTED},}selected-${sf_name}"
    fi
    echo "[campaign] scaffold finished: $AGENT_NAME ($SCAFFOLD_JOBS)" | tee -a "$LOG_FILE"
  done 3< <(python3 "$BLOCK_DIR/scripts/validate_scaffolds.py" --config "$CONFIG" --tsv)
  [[ "$PRINT_COMMAND_ONLY" != "1" ]] || exit 0
  MERGE_FLAGS=""
  if [[ -n "$SFT_ROOT_FLAG" ]]; then
    MERGE_FLAGS="${SFT_ROOT_FLAG} --out-dir ${SFT_OUT_DIR%/}/merged"
  fi
  echo "" | tee -a "$LOG_FILE"
  echo "Campaign complete ($CAMPAIGN_JOBS)." | tee -a "$LOG_FILE"
  if [[ "$SFT_CONVERT_ENABLED" != "true" ]]; then
    echo "SFT conversion is disabled — convert each job (scripts/convert_trajectories.sh --job <job> --scaffold <key>) before selecting or merging." | tee -a "$LOG_FILE"
  elif [[ "$SAMPLES_PER_TASK" -gt 1 ]]; then
    echo "Select the best records per task within each scaffold:" | tee -a "$LOG_FILE"
    printf '%s' "$CAMPAIGN_SELECT_HINTS" | tee -a "$LOG_FILE"
    echo "Then merge the per-scaffold selections into the campaign dataset:" | tee -a "$LOG_FILE"
    echo "  python3 scripts/merge_sft_data.py --jobs ${CAMPAIGN_SELECTED}${MERGE_FLAGS}" | tee -a "$LOG_FILE"
  else
    echo "Merge the per-scaffold SFT jobs into the campaign dataset with:" | tee -a "$LOG_FILE"
    echo "  python3 scripts/merge_sft_data.py --jobs ${CAMPAIGN_JOBS}${MERGE_FLAGS}" | tee -a "$LOG_FILE"
  fi
else
  run_scaffold_jobs \
    "$(cfg runtime_info.input.agent.name)" \
    "$(cfg runtime_info.input.agent.version)" \
    "$(cfg runtime_info.input.agent.runtime_image)" \
    "$(cfg runtime_info.input.agent.runtime_host_path)" \
    "auto"
  [[ "$PRINT_COMMAND_ONLY" != "1" ]] || exit 0
  echo "" | tee -a "$LOG_FILE"
  if [[ "$SAMPLES_PER_TASK" -gt 1 ]]; then
    echo "tracer campaign complete: $SAMPLES_PER_TASK sample job(s) under $HARBOR_JOBS_DIR_RAW" | tee -a "$LOG_FILE"
    if [[ "$SFT_CONVERT_ENABLED" == "true" ]]; then
      SELECT_OUT_FLAG=""
      if [[ -n "$SFT_ROOT_FLAG" ]]; then
        SELECT_OUT_FLAG=" --out-dir ${SFT_OUT_DIR%/}/selected"
      fi
      echo "Select the best records per task: python3 scripts/select_trajectories.py --jobs ${SCAFFOLD_JOBS} --per-task-cap 2${SFT_ROOT_FLAG}${SELECT_OUT_FLAG}" | tee -a "$LOG_FILE"
    else
      echo "SFT conversion is disabled; convert each sample job first (scripts/convert_trajectories.sh --job <job>-s<i>) before best-of-n selection." | tee -a "$LOG_FILE"
    fi
  else
    echo "tracer complete. Expected job dir: $JOB_DIR_RAW" | tee -a "$LOG_FILE"
    echo "Expected trajectory files: $TRAJGEN_TRAJECTORY_FILE_PATTERN" | tee -a "$LOG_FILE"
  fi
fi
