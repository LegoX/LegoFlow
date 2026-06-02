#!/usr/bin/env bash
# Validate trajgen config and local Harbor repo state without running rollouts.
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

cfg_list() {
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
    sys.exit(0)
if not isinstance(value, list):
    print(f"ERROR: {dotted_key} must be a list", file=sys.stderr)
    sys.exit(2)
for item in value:
    print("" if item is None else item)
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

validate_task_root() {
  local root="$1"
  python3 - "$root" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
if not root.is_dir():
    print("missing")
    raise SystemExit(1)

required_files = ["task.toml", "instruction.md"]
required_dirs = ["environment", "tests"]
count = 0
for child in root.iterdir():
    if not child.is_dir():
        continue
    if all((child / name).is_file() for name in required_files) and all(
        (child / name).is_dir() for name in required_dirs
    ):
        count += 1

if count == 0:
    print("invalid")
    raise SystemExit(1)

print(count)
PY
}

echo "=== trajgen dryrun: $BLOCK_DIR ==="
echo ""

echo "--- 1. Block files ---"
# Note: meta_info and status are merged into config.yaml in this block, so
# standalone metainfo.yaml / status.yaml are not expected.
for file in CLAUDE.md config.yaml docs/index.mdx artifacts/index.yaml; do
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
  SWE_DP_URL="$(cfg meta_info.repositories.swe_data_process.url)"
  SWE_DP_BRANCH="$(cfg meta_info.repositories.swe_data_process.branch)"
  SWE_DP_REF="$(cfg meta_info.repositories.swe_data_process.ref)"
  SWE_DP_COMMIT="$(cfg meta_info.repositories.swe_data_process.commit)"
  SWE_DP_PATH_RAW="$(cfg meta_info.repositories.swe_data_process.path)"
  SWE_DP_READONLY="$(cfg meta_info.repositories.swe_data_process.readonly)"
  UV_PROJECT_ENVIRONMENT_RAW="$(cfg meta_info.environment.harbor_uv)"
  SWE_DP_UV_RAW="$(cfg meta_info.environment.swe_data_process_uv)"
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
  SWE_DP_URL=""
  SWE_DP_BRANCH=""
  SWE_DP_REF=""
  SWE_DP_COMMIT=""
  SWE_DP_PATH_RAW=""
  SWE_DP_READONLY=""
  UV_PROJECT_ENVIRONMENT_RAW=""
  SWE_DP_UV_RAW=""
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

check_managed_repo() {
  # Args: NAME URL BRANCH REF COMMIT PATH_RAW READONLY OUT_DIR_VAR
  local NAME="$1"
  local R_URL="$2"
  local R_BRANCH="$3"
  local R_REF="$4"
  local R_COMMIT="$5"
  local R_PATH_RAW="$6"
  local R_READONLY="$7"
  local OUT_DIR_VAR="$8"

  echo ""
  echo "--- $NAME repo config ---"
  [[ -n "$R_URL" ]] && ok "meta_info.repositories.$NAME.url = $R_URL" || fail "meta_info.repositories.$NAME.url is empty"
  if [[ -n "$R_COMMIT" ]]; then
    ok "meta_info.repositories.$NAME.commit = $R_COMMIT"
  elif [[ -n "$R_REF" ]]; then
    ok "meta_info.repositories.$NAME.ref = $R_REF"
  elif [[ -n "$R_BRANCH" ]]; then
    ok "meta_info.repositories.$NAME.branch = $R_BRANCH"
  else
    fail "meta_info.repositories.$NAME.branch/ref or commit is required"
  fi
  [[ -n "$R_PATH_RAW" ]] && ok "meta_info.repositories.$NAME.path = $R_PATH_RAW" || fail "meta_info.repositories.$NAME.path is empty"
  [[ "$R_READONLY" == "true" || "$R_READONLY" == "false" ]] && ok "meta_info.repositories.$NAME.readonly = $R_READONLY" || fail "meta_info.repositories.$NAME.readonly must be true or false"

  if [[ -n "$R_PATH_RAW" ]]; then
    if git -C "$BLOCK_DIR" check-ignore -q "$R_PATH_RAW" 2>/dev/null; then
      ok "$R_PATH_RAW is gitignored"
    else
      warn "$R_PATH_RAW is not reported as gitignored"
    fi
  fi

  echo ""
  echo "--- $NAME local checkout ---"
  if [[ -n "$R_PATH_RAW" ]]; then
    local R_DIR
    R_DIR="$(abspath "$R_PATH_RAW")"
    # Export to caller for downstream sections (Harbor needs HARBOR_DIR; swe_dp needs SWE_DP_DIR)
    printf -v "$OUT_DIR_VAR" '%s' "$R_DIR"
    export "$OUT_DIR_VAR"
    if [[ -e "$R_DIR/.git" ]]; then
      ok "$R_PATH_RAW exists"
      local CURRENT_URL
      local COMMIT
      CURRENT_URL="$(git -C "$R_DIR" remote get-url origin 2>/dev/null || true)"
      COMMIT="$(git -C "$R_DIR" rev-parse HEAD 2>/dev/null || true)"
      [[ "$CURRENT_URL" == "$R_URL" ]] && ok "origin URL matches config.yaml" || fail "origin URL mismatch: $CURRENT_URL"
      [[ -n "$COMMIT" ]] && ok "current commit: $COMMIT" || fail "cannot resolve current commit"
      if [[ -n "$R_COMMIT" ]]; then
        [[ "$COMMIT" == "$R_COMMIT" ]] && ok "current commit matches config.yaml pin" || fail "current commit $COMMIT does not match config.yaml pin $R_COMMIT"
      elif [[ -n "$R_REF" || -n "$R_BRANCH" ]]; then
        local EXPECTED_REF
        local EXPECTED_COMMIT
        EXPECTED_REF="${R_REF:-$R_BRANCH}"
        EXPECTED_COMMIT="$(git -C "$R_DIR" rev-parse "origin/${EXPECTED_REF}^{commit}" 2>/dev/null || true)"
        if [[ -n "$EXPECTED_COMMIT" ]]; then
          [[ "$COMMIT" == "$EXPECTED_COMMIT" ]] && ok "current commit matches origin/$EXPECTED_REF" || fail "current commit $COMMIT does not match origin/$EXPECTED_REF ($EXPECTED_COMMIT)"
        else
          warn "could not resolve origin/$EXPECTED_REF for branch consistency check"
        fi
      fi
      if [[ -n "$(git -C "$R_DIR" status --porcelain 2>/dev/null || true)" ]]; then
        fail "$R_PATH_RAW has local modifications"
      else
        ok "$R_PATH_RAW worktree is clean"
      fi
      if [[ "$R_READONLY" == "true" ]]; then
        local WRITABLE_COUNT
        WRITABLE_COUNT="$(python3 - "$R_DIR" <<'PY'
import os
import stat
import sys

root = sys.argv[1]
count = 0
write_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
for dirpath, dirnames, filenames in os.walk(root):
    if ".git" in dirnames:
        dirnames.remove(".git")
    for name in dirnames + filenames:
        if name == ".git":
            continue
        path = os.path.join(dirpath, name)
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            continue
        if mode & write_bits:
            count += 1
            if count > 20:
                break
    if count > 20:
        break
print(count)
PY
)"
        if [[ "$WRITABLE_COUNT" == "0" ]]; then
          ok "$NAME working tree is read-only"
        else
          warn "$NAME working tree has writable paths ($WRITABLE_COUNT sampled)"
        fi
      fi
    else
      warn "$R_PATH_RAW is missing; run bash scripts/update_repos.sh --repo $NAME"
    fi
  fi
}

echo ""
echo "=== 3+4. Managed repos ==="
check_managed_repo "harbor" "$HARBOR_URL" "$HARBOR_BRANCH" "$HARBOR_REF" "$HARBOR_COMMIT" "$HARBOR_PATH_RAW" "$READONLY" HARBOR_DIR
check_managed_repo "swe_data_process" "$SWE_DP_URL" "$SWE_DP_BRANCH" "$SWE_DP_REF" "$SWE_DP_COMMIT" "$SWE_DP_PATH_RAW" "$SWE_DP_READONLY" SWE_DP_DIR

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
if run_in_harbor_env "$PYTHON_CHECK_COMMAND -c 'import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\")'" >/tmp/trajgen_python_version.$$ 2>/tmp/trajgen_python_error.$$; then
  PY_VERSION="$(tr -d '\n' </tmp/trajgen_python_version.$$)"
  ok "python check command works: $PYTHON_CHECK_COMMAND ($PY_VERSION)"
  if run_in_harbor_env "$PYTHON_CHECK_COMMAND -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'" >/dev/null 2>&1; then
    ok "python version >= 3.12"
  else
    fail "python version must be >= 3.12 (found ${PY_VERSION:-unknown})"
  fi
else
  ERR="$(tr '\n' ' ' </tmp/trajgen_python_error.$$ 2>/dev/null || true)"
  fail "python check command failed: $PYTHON_CHECK_COMMAND ${ERR:+($ERR)}"
fi
rm -f /tmp/trajgen_python_version.$$ /tmp/trajgen_python_error.$$

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

if run_in_harbor_env "timeout 15 $HARBOR_CHECK_COMMAND --help >/dev/null" 2>/dev/null; then
  ok "harbor check command works: $HARBOR_CHECK_COMMAND"
else
  fail "harbor check command failed or timed out (>15s): $HARBOR_CHECK_COMMAND"
fi
else
  warn "skipping Harbor import/CLI health checks because the configured uv environment is missing"
fi

echo ""
echo "--- 5b. swe_data_process Environment ---"
# swe_data_process is only needed for the optional post-Harbor SFT conversion.
# When sft_conversion.enabled is false, a missing/broken env must not block
# trajectory generation, so downgrade these checks to warnings in that case.
SFT_ENABLED_PRECHECK="$(cfg runtime_info.input.sft_conversion.enabled)"
if [[ "$SFT_ENABLED_PRECHECK" == "true" ]]; then
  swe_dp_problem() { fail "$1"; }
else
  swe_dp_problem() { warn "$1 (non-blocking: sft_conversion.enabled is not true)"; }
fi
if [[ -n "$SWE_DP_UV_RAW" ]]; then
  SWE_DP_UV_ABS="$(abspath "$SWE_DP_UV_RAW")"
  SWE_DP_PYTHON="$SWE_DP_UV_ABS/bin/python"
  ok "swe_data_process uv environment path = $SWE_DP_UV_RAW"
  if [[ -n "${SWE_DP_DIR:-}" && -e "$SWE_DP_DIR/.git" ]]; then
    case "$SWE_DP_UV_ABS" in
      "$SWE_DP_DIR"/*)
        if [[ "$SWE_DP_READONLY" == "true" ]]; then
          fail "environment.swe_data_process_uv must be outside repos/swe_data_process when the repo is read-only"
        else
          warn "environment.swe_data_process_uv is inside repos/swe_data_process; this is only safe while repositories.swe_data_process.readonly is false"
        fi
        ;;
      *)
        ok "swe_data_process uv environment is outside repos/swe_data_process"
        ;;
    esac
  fi
  if [[ -d "$SWE_DP_UV_ABS" ]]; then
    ok "swe_data_process uv environment exists"
    if [[ -x "$SWE_DP_PYTHON" ]]; then
      ok "swe_data_process python exists: $SWE_DP_PYTHON"
      if "$SWE_DP_PYTHON" -c "import swe_data_process" >/dev/null 2>&1; then
        ok "swe_data_process package is importable"
      else
        swe_dp_problem "swe_data_process package is not importable; from repos/swe_data_process run: UV_PROJECT_ENVIRONMENT=$SWE_DP_UV_ABS uv sync --extra llm  (or use bash scripts/setup_swe_data_process_env.sh)"
      fi
    else
      swe_dp_problem "swe_data_process python not found: $SWE_DP_PYTHON"
    fi
  else
    swe_dp_problem "swe_data_process uv environment is missing; run bash scripts/setup_swe_data_process_env.sh"
  fi
else
  fail "environment.swe_data_process_uv is required"
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
    fail "litellm uv environment is missing; create it with: uv venv $LITELLM_UV_ABS --python ${LITELLM_PYTHON_VERSION:-3.13} && uv pip install --python $LITELLM_UV_ABS/bin/python 'litellm[proxy]==${LITELLM_VERSION:-1.83.9}'"
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
HARBOR_DATASET_PATH="$(cfg runtime_info.input.harbor_job.dataset_path)"
TASK_SOURCE_DATASET_NAME="$(cfg runtime_info.input.task_source.dataset_name)"
HARBOR_DATASET="$(cfg runtime_info.input.harbor_job.dataset)"
if [[ -z "$HARBOR_DATASET" && -n "$TASK_SOURCE_DATASET_NAME" ]]; then
  HARBOR_DATASET="$(basename "$TASK_SOURCE_DATASET_NAME")"
fi
if [[ -z "$HARBOR_DATASET_PATH" && -n "$HARBOR_DATASET" ]]; then
  HARBOR_DATASET_PATH="artifacts/tasks/$HARBOR_DATASET"
fi
[[ -n "$HARBOR_DATASET" ]] && ok "derived harbor dataset = $HARBOR_DATASET" || fail "could not derive harbor dataset from runtime_info.input.task_source.dataset_name"
[[ -n "$HARBOR_DATASET_PATH" ]] && ok "derived harbor dataset_path = $HARBOR_DATASET_PATH" || fail "could not derive harbor dataset_path"
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
case "$HARBOR_DATASET_PATH" in
  artifacts/tasks|artifacts/tasks/*|/*/artifacts/tasks|/*/artifacts/tasks/*)
    ok "Harbor dataset_path is under block artifacts/tasks"
    ;;
  *)
    fail "harbor_job.dataset_path should point under artifacts/tasks, got: $HARBOR_DATASET_PATH"
    ;;
esac
HARBOR_DATASET_PATH_ABS="$(abspath "$HARBOR_DATASET_PATH")"
if TASK_COUNT="$(validate_task_root "$HARBOR_DATASET_PATH_ABS" 2>/dev/null)"; then
  ok "Harbor task directory is ready ($TASK_COUNT task dirs)"
else
  fail "Harbor tasks are not prepared at $HARBOR_DATASET_PATH; run bash scripts/prepare_tasks.sh"
fi
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

echo ""
echo "--- 8b. SFT conversion config ---"
SFT_ENABLED="$(cfg runtime_info.input.sft_conversion.enabled)"
if [[ "$SFT_ENABLED" == "true" || "$SFT_ENABLED" == "false" ]]; then
  ok "runtime_info.input.sft_conversion.enabled = $SFT_ENABLED"
else
  fail "runtime_info.input.sft_conversion.enabled must be true or false (got: '${SFT_ENABLED:-<empty>}')"
fi
SFT_SCAFFOLD="$(cfg runtime_info.input.sft_conversion.scaffold)"
case "$SFT_SCAFFOLD" in
  auto|claude_code|open_code|openhands_sdk|terminus2)
    ok "runtime_info.input.sft_conversion.scaffold = $SFT_SCAFFOLD"
    ;;
  "")
    fail "runtime_info.input.sft_conversion.scaffold is required (one of: auto, claude_code, open_code, openhands_sdk, terminus2)"
    ;;
  *)
    fail "runtime_info.input.sft_conversion.scaffold must be one of: auto, claude_code, open_code, openhands_sdk, terminus2 (got: $SFT_SCAFFOLD)"
    ;;
esac
SFT_OUT_DIR="$(cfg runtime_info.input.sft_conversion.out_dir)"
[[ -n "$SFT_OUT_DIR" ]] && ok "runtime_info.input.sft_conversion.out_dir = $SFT_OUT_DIR" || fail "runtime_info.input.sft_conversion.out_dir is required"
SFT_DATA_DIR_OUT="$(cfg runtime_info.output.sft_data_dir.path)"
[[ -n "$SFT_DATA_DIR_OUT" ]] && ok "runtime_info.output.sft_data_dir.path = $SFT_DATA_DIR_OUT" || fail "runtime_info.output.sft_data_dir.path is required"

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
