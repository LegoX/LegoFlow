#!/usr/bin/env bash
# Validate eval config and local Harbor repo state without running rollouts.
# Mirrors trajgen/scripts/dryrun.sh, but task_source is registry-based (no
# artifacts/tasks staging) so dataset-existence checks happen against
# repos/harbor/registry.json instead of a local task directory.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

PASS=0
FAIL=0
WARN=0

ok()   { echo "  [OK]   $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $1"; WARN=$((WARN+1)); }
info() { echo "  [INFO] $1"; }

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

run_in_harbor_env() {
  local command="$1"
  if [[ -z "${HARBOR_DIR:-}" || ! -d "$HARBOR_DIR" ]]; then
    return 1
  fi
  (
    cd "$HARBOR_DIR"
    if [[ -n "${UV_PROJECT_ENVIRONMENT_ABS:-}" ]]; then
      export UV_PROJECT_ENVIRONMENT="$UV_PROJECT_ENVIRONMENT_ABS"
    fi
    export HARBOR_EDITABLE_ROOT="$HARBOR_DIR"
    bash -lc "$command"
  )
}

# Confirm the configured (dataset_name, version) pair exists in the Harbor
# registry, and report the task count Harbor would expand to.
check_registry_dataset() {
  local registry_path="$1"
  local dataset_name="$2"
  local version="$3"
  python3 - "$registry_path" "$dataset_name" "$version" <<'PY'
import json
import sys
from pathlib import Path

registry_path, dataset_name, version = sys.argv[1:4]
path = Path(registry_path)
if not path.is_file():
    print("missing")
    sys.exit(1)
try:
    entries = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"invalid:{exc}")
    sys.exit(1)
if not isinstance(entries, list):
    print("unexpected_shape")
    sys.exit(1)
match = None
for entry in entries:
    if not isinstance(entry, dict):
        continue
    if entry.get("name") != dataset_name:
        continue
    if version and entry.get("version") != version:
        continue
    match = entry
    break
if match is None:
    print("not_found")
    sys.exit(1)
n_tasks = len(match.get("tasks", []) or [])
print(f"ok:{match.get('name')}@{match.get('version')}:{n_tasks}")
PY
}

echo "=== eval dryrun: $BLOCK_DIR ==="
echo ""

echo "--- 1. Block files ---"
for file in CLAUDE.md config.yaml dashboard/overview.mdx artifacts/index.yaml; do
  if [[ -f "$BLOCK_DIR/$file" ]]; then
    ok "$file exists"
  else
    fail "$file missing"
  fi
done

echo ""
echo "--- 2. YAML syntax ---"
for file in "$CONFIG" "$BLOCK_DIR/artifacts/index.yaml"; do
  if python3 - "$file" <<'PY' >/dev/null 2>&1
import sys
import yaml
with open(sys.argv[1], encoding="utf-8") as fh:
    yaml.safe_load(fh)
PY
  then
    ok "$(basename "$file") parses"
  else
    fail "$(basename "$file") has YAML errors or PyYAML is unavailable"
  fi
done

if [[ $FAIL -eq 0 ]]; then
  HARBOR_URL="$(cfg meta_info.repositories.harbor.url)"
  HARBOR_BRANCH="$(cfg meta_info.repositories.harbor.branch)"
  HARBOR_REF="$(cfg meta_info.repositories.harbor.ref)"
  HARBOR_COMMIT="$(cfg meta_info.repositories.harbor.commit)"
  HARBOR_PATH_RAW="$(cfg meta_info.repositories.harbor.path)"
  READONLY="$(cfg meta_info.repositories.harbor.readonly)"
  UV_PROJECT_ENVIRONMENT_RAW="$(cfg meta_info.environment.harbor_uv)"
  LITELLM_UV_RAW="$(cfg meta_info.environment.litellm_uv)"
  LITELLM_PYTHON_VERSION="$(cfg meta_info.environment.litellm.python_version)"
  LITELLM_VERSION="$(cfg meta_info.environment.litellm.litellm_version)"
  MODEL_API_BASE_URL="$(cfg runtime_info.input.llm_api.api_base_url)"
  if [[ -z "$MODEL_API_BASE_URL" ]]; then
    MODEL_API_BASE_URL="$(cfg runtime_info.input.llm_api.api_base)"
  fi
  MODEL_API_MODEL="$(cfg runtime_info.input.llm_api.model)"
  if [[ -z "$MODEL_API_MODEL" ]]; then
    MODEL_API_MODEL="$(cfg runtime_info.input.llm_api.upstream_model)"
  fi
  MODEL_API_KEY="$(cfg runtime_info.input.llm_api.api_key)"
  MODEL_API_INPUT_COST="$(cfg runtime_info.input.llm_api.input_cost_per_token)"
  MODEL_API_OUTPUT_COST="$(cfg runtime_info.input.llm_api.output_cost_per_token)"
  RUN_COMMAND="$(cfg command_override)"
else
  HARBOR_URL=""
  HARBOR_BRANCH=""
  HARBOR_REF=""
  HARBOR_COMMIT=""
  HARBOR_PATH_RAW=""
  READONLY=""
  UV_PROJECT_ENVIRONMENT_RAW=""
  LITELLM_UV_RAW=""
  LITELLM_PYTHON_VERSION=""
  LITELLM_VERSION=""
  MODEL_API_BASE_URL=""
  MODEL_API_MODEL=""
  MODEL_API_KEY=""
  MODEL_API_INPUT_COST=""
  MODEL_API_OUTPUT_COST=""
  RUN_COMMAND=""
fi

echo ""
echo "--- 3. Harbor repo config ---"
[[ -n "$HARBOR_URL" ]] && ok "meta_info.repositories.harbor.url = $HARBOR_URL" || fail "meta_info.repositories.harbor.url is empty"
if [[ -n "$HARBOR_COMMIT" ]]; then
  ok "meta_info.repositories.harbor.commit = $HARBOR_COMMIT"
elif [[ -n "$HARBOR_REF" ]]; then
  ok "meta_info.repositories.harbor.ref = $HARBOR_REF"
elif [[ -n "$HARBOR_BRANCH" ]]; then
  ok "meta_info.repositories.harbor.branch = $HARBOR_BRANCH"
else
  fail "meta_info.repositories.harbor.branch/ref or commit is required"
fi
[[ -n "$HARBOR_PATH_RAW" ]] && ok "meta_info.repositories.harbor.path = $HARBOR_PATH_RAW" || fail "meta_info.repositories.harbor.path is empty"
[[ "$READONLY" == "true" || "$READONLY" == "false" ]] && ok "meta_info.repositories.harbor.readonly = $READONLY" || fail "meta_info.repositories.harbor.readonly must be true or false"

if [[ -n "$HARBOR_PATH_RAW" ]]; then
  if git -C "$BLOCK_DIR" check-ignore -q "$HARBOR_PATH_RAW" 2>/dev/null; then
    ok "$HARBOR_PATH_RAW is gitignored"
  else
    warn "$HARBOR_PATH_RAW is not reported as gitignored"
  fi
fi

echo ""
echo "--- 4. Local Harbor checkout ---"
if [[ -n "$HARBOR_PATH_RAW" ]]; then
  HARBOR_DIR="$(abspath "$HARBOR_PATH_RAW")"
  if [[ -e "$HARBOR_DIR/.git" ]]; then
    ok "$HARBOR_PATH_RAW exists"
    CURRENT_URL="$(git -C "$HARBOR_DIR" remote get-url origin 2>/dev/null || true)"
    COMMIT="$(git -C "$HARBOR_DIR" rev-parse HEAD 2>/dev/null || true)"
    [[ "$CURRENT_URL" == "$HARBOR_URL" ]] && ok "origin URL matches config.yaml" || fail "origin URL mismatch: $CURRENT_URL"
    [[ -n "$COMMIT" ]] && ok "current commit: $COMMIT" || fail "cannot resolve current commit"
    if [[ -n "$HARBOR_COMMIT" ]]; then
      [[ "$COMMIT" == "$HARBOR_COMMIT" ]] && ok "current commit matches config.yaml pin" || fail "current commit $COMMIT does not match config.yaml pin $HARBOR_COMMIT"
    elif [[ -n "$HARBOR_REF" || -n "$HARBOR_BRANCH" ]]; then
      EXPECTED_REF="${HARBOR_REF:-$HARBOR_BRANCH}"
      EXPECTED_COMMIT="$(git -C "$HARBOR_DIR" rev-parse "origin/${EXPECTED_REF}^{commit}" 2>/dev/null || true)"
      if [[ -n "$EXPECTED_COMMIT" ]]; then
        [[ "$COMMIT" == "$EXPECTED_COMMIT" ]] && ok "current commit matches origin/$EXPECTED_REF" || fail "current commit $COMMIT does not match origin/$EXPECTED_REF ($EXPECTED_COMMIT)"
      else
        warn "could not resolve origin/$EXPECTED_REF for branch consistency check"
      fi
    fi
    if [[ -n "$(git -C "$HARBOR_DIR" status --porcelain 2>/dev/null || true)" ]]; then
      fail "$HARBOR_PATH_RAW has local modifications"
    else
      ok "$HARBOR_PATH_RAW worktree is clean"
    fi
  else
    warn "$HARBOR_PATH_RAW is missing; run bash scripts/update_repos.sh"
  fi
fi

echo ""
echo "--- 5. Environment ---"
ENV_READY=1

if [[ -n "$UV_PROJECT_ENVIRONMENT_RAW" ]]; then
  UV_PROJECT_ENVIRONMENT_ABS="$(abspath "$UV_PROJECT_ENVIRONMENT_RAW")"
  PYTHON_CHECK_COMMAND="$UV_PROJECT_ENVIRONMENT_ABS/bin/python"
  HARBOR_CHECK_COMMAND="$UV_PROJECT_ENVIRONMENT_ABS/bin/harbor"
  INSTALL_COMMAND="UV_PROJECT_ENVIRONMENT=$UV_PROJECT_ENVIRONMENT_ABS uv sync --all-extras"
  ok "uv project environment path = $UV_PROJECT_ENVIRONMENT_RAW"
  if [[ -n "${HARBOR_DIR:-}" && -e "$HARBOR_DIR/.git" ]]; then
    case "$UV_PROJECT_ENVIRONMENT_ABS" in
      "$HARBOR_DIR"/*)
        if [[ "$READONLY" == "true" ]]; then
          fail "environment.harbor_uv must be outside repos/harbor when the repo is read-only"
        else
          warn "environment.harbor_uv is inside repos/harbor; this is only safe while repositories.harbor.readonly is false"
        fi
        ;;
      *)
        ok "uv project environment is outside repos/harbor"
        ;;
    esac
  fi
  if [[ -d "$UV_PROJECT_ENVIRONMENT_ABS" ]]; then
    ok "uv project environment exists"
  else
    fail "uv project environment is missing; from repos/harbor run: $INSTALL_COMMAND"
    ENV_READY=0
  fi
else
  fail "environment.harbor_uv is required"
  ENV_READY=0
fi

for cmd in git uv; do
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "command exists: $cmd"
  else
    fail "command missing: $cmd"
  fi
done

if [[ "$ENV_READY" == "1" ]]; then
if run_in_harbor_env "$PYTHON_CHECK_COMMAND -c 'import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\")'" >/tmp/eval_python_version.$$ 2>/tmp/eval_python_error.$$; then
  PY_VERSION="$(tr -d '\n' </tmp/eval_python_version.$$)"
  ok "python check command works: $PYTHON_CHECK_COMMAND ($PY_VERSION)"
  if run_in_harbor_env "$PYTHON_CHECK_COMMAND -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'" >/dev/null 2>&1; then
    ok "python version >= 3.12"
  else
    fail "python version must be >= 3.12 (found ${PY_VERSION:-unknown})"
  fi
else
  ERR="$(tr '\n' ' ' </tmp/eval_python_error.$$ 2>/dev/null || true)"
  fail "python check command failed: $PYTHON_CHECK_COMMAND ${ERR:+($ERR)}"
fi
rm -f /tmp/eval_python_version.$$ /tmp/eval_python_error.$$

for pkg in harbor litellm datasets; do
  MOD_NAME="${pkg//-/_}"
  if run_in_harbor_env "$PYTHON_CHECK_COMMAND -c 'import importlib; importlib.import_module(\"$MOD_NAME\")'" >/dev/null 2>&1; then
    ok "python package importable via configured environment: $pkg"
  else
    fail "python package not importable via configured environment: $pkg"
  fi
done

if [[ -n "${HARBOR_DIR:-}" && -e "$HARBOR_DIR/.git" ]]; then
  EDITABLE_STATUS="$(run_in_harbor_env "$PYTHON_CHECK_COMMAND \"$BLOCK_DIR/scripts/check_harbor_editable.py\"" 2>/dev/null || true)"
  case "$EDITABLE_STATUS" in
    ok:*) ok "harbor imports from repos/harbor" ;;
    mismatch:*) fail "harbor import is not from repos/harbor (${EDITABLE_STATUS#mismatch:})" ;;
    import_error:*) fail "harbor import failed (${EDITABLE_STATUS#import_error:})" ;;
    *) fail "could not verify harbor editable install" ;;
  esac
fi

if run_in_harbor_env "$HARBOR_CHECK_COMMAND --help >/dev/null" 2>/dev/null; then
  ok "harbor check command works: $HARBOR_CHECK_COMMAND"
else
  fail "harbor check command failed: $HARBOR_CHECK_COMMAND"
fi
else
  warn "skipping Harbor import/CLI health checks because the configured uv environment is missing"
fi

echo ""
echo "--- 6. LiteLLM Environment ---"
if [[ -n "$LITELLM_UV_RAW" ]]; then
  LITELLM_UV_ABS="$(abspath "$LITELLM_UV_RAW")"
  LITELLM_PYTHON="$LITELLM_UV_ABS/bin/python"
  LITELLM_BIN="$LITELLM_UV_ABS/bin/litellm"
  ok "litellm uv environment path = $LITELLM_UV_RAW"
  if [[ -d "$LITELLM_UV_ABS" ]]; then
    ok "litellm uv environment exists"
  else
    fail "litellm uv environment is missing; create it with: uv venv $LITELLM_UV_ABS --python ${LITELLM_PYTHON_VERSION:-3.13} && uv pip install --python $LITELLM_UV_ABS/bin/python 'litellm[proxy]==${LITELLM_VERSION:-1.83.14}'"
  fi
else
  fail "environment.litellm_uv is required"
  LITELLM_PYTHON="python"
  LITELLM_BIN="litellm"
fi

if command -v "$LITELLM_PYTHON" >/dev/null 2>&1; then
  ok "litellm python exists: $LITELLM_PYTHON"
  LITELLM_PY_ACTUAL="$("$LITELLM_PYTHON" - <<'PY' 2>/dev/null || true
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
  if [[ -n "$LITELLM_PYTHON_VERSION" ]]; then
    [[ "$LITELLM_PY_ACTUAL" == "$LITELLM_PYTHON_VERSION" ]] && ok "litellm python version = $LITELLM_PY_ACTUAL" || fail "litellm python version expected $LITELLM_PYTHON_VERSION, found ${LITELLM_PY_ACTUAL:-unknown}"
  else
    ok "litellm python version = ${LITELLM_PY_ACTUAL:-unknown}"
  fi
else
  fail "litellm python not found: $LITELLM_PYTHON"
fi

if command -v "$LITELLM_BIN" >/dev/null 2>&1; then
  ok "litellm CLI exists: $LITELLM_BIN"
else
  fail "litellm CLI not found: $LITELLM_BIN"
fi

if command -v "$LITELLM_PYTHON" >/dev/null 2>&1; then
  LITELLM_VERSION_STATUS="$("$LITELLM_PYTHON" - <<'PY' 2>/dev/null || true
import importlib.metadata

try:
    installed = importlib.metadata.version("litellm")
except importlib.metadata.PackageNotFoundError:
    print("missing")
    raise SystemExit(0)

print(installed)
PY
)"
  if [[ "$LITELLM_VERSION_STATUS" == "missing" ]]; then
    fail "litellm package is not installed in $LITELLM_PYTHON"
  elif [[ -z "$LITELLM_VERSION_STATUS" ]]; then
    fail "could not check litellm package version with $LITELLM_PYTHON"
  elif [[ -n "$LITELLM_VERSION" ]]; then
    [[ "$LITELLM_VERSION_STATUS" == "$LITELLM_VERSION" ]] && ok "litellm package version = $LITELLM_VERSION_STATUS" || fail "litellm package version expected $LITELLM_VERSION, found $LITELLM_VERSION_STATUS"
  else
    ok "litellm package version = $LITELLM_VERSION_STATUS"
  fi
fi

echo ""
echo "--- 7. Model API ---"
[[ -n "$MODEL_API_BASE_URL" ]] && ok "runtime_info.input.llm_api.api_base_url = $MODEL_API_BASE_URL" || fail "runtime_info.input.llm_api.api_base_url is required"
[[ -n "$MODEL_API_MODEL" ]] && ok "runtime_info.input.llm_api.model = $MODEL_API_MODEL" || fail "runtime_info.input.llm_api.model is required"
if [[ -n "$MODEL_API_KEY" ]]; then
  ok "runtime_info.input.llm_api.api_key is set"
else
  fail "runtime_info.input.llm_api.api_key is required"
fi
[[ -n "$MODEL_API_INPUT_COST" ]] && ok "runtime_info.input.llm_api.input_cost_per_token = $MODEL_API_INPUT_COST" || warn "runtime_info.input.llm_api.input_cost_per_token is empty"
[[ -n "$MODEL_API_OUTPUT_COST" ]] && ok "runtime_info.input.llm_api.output_cost_per_token = $MODEL_API_OUTPUT_COST" || warn "runtime_info.input.llm_api.output_cost_per_token is empty"
info "llm_api is raw upstream config; LiteLLM reachability is checked by each job after proxy startup"

echo ""
echo "--- 8. Harbor run config ---"
for key in \
  runtime_info.input.litellm_proxy.config_template \
  runtime_info.input.litellm_proxy.port \
  runtime_info.input.litellm_proxy.master_key \
  runtime_info.input.task_source.provider \
  runtime_info.input.task_source.dataset_name \
  runtime_info.input.harbor_job.jobs_dir \
  runtime_info.input.harbor_job.n_concurrent \
  runtime_info.input.harbor_job.max_retries \
  runtime_info.input.harbor_job.timeout_multiplier \
  runtime_info.input.agent.name \
  runtime_info.input.agent.version; do
  value="$(cfg "$key")"
  [[ -n "$value" ]] && ok "$key = $value" || fail "$key is required"
done

PROVIDER="$(cfg runtime_info.input.task_source.provider)"
case "$PROVIDER" in
  harbor_registry)
    ok "task_source.provider = harbor_registry (Harbor will load tasks via --dataset + --registry-path)"
    ;;
  local|huggingface)
    warn "task_source.provider=$PROVIDER is not the eval-block default; expected harbor_registry"
    ;;
  *)
    fail "unsupported task_source.provider for eval block: $PROVIDER (expected harbor_registry)"
    ;;
esac

DATASET_NAME="$(cfg runtime_info.input.task_source.dataset_name)"
DATASET_VERSION="$(cfg runtime_info.input.task_source.version)"
REGISTRY_RAW="$(cfg runtime_info.input.task_source.registry_path)"
if [[ -z "$REGISTRY_RAW" && -n "$HARBOR_PATH_RAW" ]]; then
  REGISTRY_RAW="$HARBOR_PATH_RAW/registry.json"
fi
[[ -n "$REGISTRY_RAW" ]] && ok "registry_path = $REGISTRY_RAW" || fail "task_source.registry_path is required (or set repos/harbor.path)"

if [[ -n "$REGISTRY_RAW" ]]; then
  REGISTRY_ABS="$(abspath "$REGISTRY_RAW")"
  if [[ -f "$REGISTRY_ABS" ]]; then
    ok "registry.json found at $REGISTRY_RAW"
    REGISTRY_CHECK="$(check_registry_dataset "$REGISTRY_ABS" "$DATASET_NAME" "$DATASET_VERSION" 2>/dev/null || true)"
    case "$REGISTRY_CHECK" in
      ok:*)
        IFS=':' read -r _ matched ntasks <<<"$REGISTRY_CHECK"
        ok "registry entry resolved: $matched ($ntasks tasks)"
        ;;
      not_found)
        fail "dataset_name='$DATASET_NAME' version='$DATASET_VERSION' not present in $REGISTRY_RAW"
        ;;
      missing)
        fail "registry.json missing at $REGISTRY_RAW"
        ;;
      *)
        fail "registry inspection failed: $REGISTRY_CHECK"
        ;;
    esac
  else
    warn "registry.json not yet present at $REGISTRY_RAW (run scripts/update_repos.sh)"
  fi
fi

LITELLM_TEMPLATE_RAW="$(cfg runtime_info.input.litellm_proxy.config_template)"
if [[ -n "$LITELLM_TEMPLATE_RAW" ]]; then
  if [[ "$LITELLM_TEMPLATE_RAW" = /* ]]; then
    LITELLM_TEMPLATE_PATH="$LITELLM_TEMPLATE_RAW"
  else
    LITELLM_TEMPLATE_PATH="${HARBOR_DIR:-}/$LITELLM_TEMPLATE_RAW"
  fi
  if [[ -n "${HARBOR_DIR:-}" && -e "$HARBOR_DIR/.git" ]]; then
    [[ -f "$LITELLM_TEMPLATE_PATH" ]] && ok "LiteLLM config template exists" || fail "LiteLLM config template not found: $LITELLM_TEMPLATE_RAW"
  else
    warn "skipping LiteLLM config template existence check because repos/harbor is missing"
  fi
fi

AGENT_MODEL_NAME="$(cfg runtime_info.input.agent.model_name)"
if [[ -z "$AGENT_MODEL_NAME" ]]; then
  AGENT_MODEL_NAME="$MODEL_API_MODEL"
  AGENT_MODEL_NAME="${AGENT_MODEL_NAME##*/}"
fi
[[ -n "$AGENT_MODEL_NAME" ]] && ok "derived agent model_name = $AGENT_MODEL_NAME" || fail "could not derive agent model_name"

HARBOR_JOBS_DIR="$(cfg runtime_info.input.harbor_job.jobs_dir)"
RUN_JOB_DIR="$(cfg job_dir)"
[[ -n "$RUN_JOB_DIR" ]] || RUN_JOB_DIR="${HARBOR_JOBS_DIR%/}/latest"
case "$HARBOR_JOBS_DIR" in
  artifacts/jobs|artifacts/jobs/*|/*/artifacts/jobs|/*/artifacts/jobs/*)
    ok "Harbor jobs_dir is under block artifacts"
    ;;
  *)
    fail "runtime_info.input.harbor_job.jobs_dir should point under artifacts/jobs, got: $HARBOR_JOBS_DIR"
    ;;
esac
case "$RUN_JOB_DIR" in
  artifacts/jobs|artifacts/jobs/*|/*/artifacts/jobs|/*/artifacts/jobs/*)
    ok "job_dir is under block artifacts"
    ;;
  *)
    fail "job_dir should point under artifacts/jobs, got: $RUN_JOB_DIR"
    ;;
esac
value="$(cfg runtime_info.input.agent.runtime_image)"
[[ -n "$value" ]] && ok "runtime_info.input.agent.runtime_image = $value" || fail "runtime_info.input.agent.runtime_image is required"

# When runtime_host_path is set, start.sh switches to a bind-mount of that
# host dir into the task container at container_runtime_root. If the dir is
# empty/missing, mount silently succeeds but the agent falls back to a
# `curl https://claude.ai/install.sh` (claude-code) or pip-install
# (openhands-sdk) step inside every task container, which generally 403s on
# isolated networks. Catch the empty-dir case here so it surfaces in
# preflight instead of a wall of exception.txt.
RUNTIME_HOST_PATH_RAW="$(cfg runtime_info.input.agent.runtime_host_path)"
AGENT_NAME_RAW="$(cfg runtime_info.input.agent.name)"
case "$AGENT_NAME_RAW" in
  custom-claude-code)
    RUNTIME_MARKER="bin/claude" ; RUNTIME_IMG_SUBPATH="claude-code" ;;
  custom-openhands-sdk)
    RUNTIME_MARKER="runtime-env.sh" ; RUNTIME_IMG_SUBPATH="oh-sdk" ;;
  custom-opencode)
    RUNTIME_MARKER="bin/opencode" ; RUNTIME_IMG_SUBPATH="opencode" ;;
  *)
    RUNTIME_MARKER="" ; RUNTIME_IMG_SUBPATH="" ;;
esac
if [[ -n "$RUNTIME_HOST_PATH_RAW" ]]; then
  RUNTIME_HOST_PATH_ABS="$(abspath "$RUNTIME_HOST_PATH_RAW")"
  EXTRACT_HINT="(extract with: CID=\$(docker create $value) && docker cp \"\$CID:/opt/custom-agent-runtime/${RUNTIME_IMG_SUBPATH:-<subpath>}\" $(dirname "$RUNTIME_HOST_PATH_RAW")/ && docker rm \"\$CID\")"
  if [[ ! -d "$RUNTIME_HOST_PATH_ABS" ]]; then
    fail "agent.runtime_host_path does not exist: $RUNTIME_HOST_PATH_RAW $EXTRACT_HINT"
  elif [[ -z "$(ls -A "$RUNTIME_HOST_PATH_ABS" 2>/dev/null)" ]]; then
    fail "agent.runtime_host_path is empty: $RUNTIME_HOST_PATH_RAW $EXTRACT_HINT"
  elif [[ -n "$RUNTIME_MARKER" && ! -e "$RUNTIME_HOST_PATH_ABS/$RUNTIME_MARKER" ]]; then
    fail "agent.runtime_host_path missing $RUNTIME_MARKER: $RUNTIME_HOST_PATH_RAW (re-extract from $value)"
  elif [[ -z "$RUNTIME_MARKER" ]]; then
    warn "agent.runtime_host_path populated but no marker defined for agent.name=$AGENT_NAME_RAW; cannot verify contents"
  else
    ok "agent.runtime_host_path is populated ($RUNTIME_MARKER present)"
  fi
fi

echo ""
echo "--- 9. Run command ---"
if [[ -n "$RUN_COMMAND" ]]; then
  ok "command_override is configured"
else
  ok "command_override is empty; scripts/start.sh will build the default Harbor command"
fi

echo ""
echo "================================================="
echo "  PASS: $PASS   WARN: $WARN   FAIL: $FAIL"
echo "================================================="
if [[ $FAIL -gt 0 ]]; then
  echo "Fix failures before running scripts/start.sh"
  exit 1
fi

echo "Dryrun complete."
