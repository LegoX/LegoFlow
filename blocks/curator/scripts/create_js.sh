#!/bin/bash
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
# python -m venv legoflow-curator-env
source artifacts/envs/legoflow-curator-env/bin/activate  # Linux/Mac
source scripts/load_runtime_env.sh
echo 'activate legoflow-curator-env'

load_runtime_env

python -c 'import legoflow_curator' 2>/dev/null || pip install -e repos/legoflow-curator/  # standalone fallback; create_all_bg.sh pre-installs once

# OPENAI_MODEL / ANTHROPIC_MODEL come from config.yaml via load_runtime_env.

set -euo pipefail

# legoflow-curator writes its state dir AND any stray tool output into the CWD;
# keep both under artifacts/ instead of polluting scripts/.
STATE_DIR="${PROJECT_ROOT}/artifacts/state/legoflow-curator-js"
mkdir -p "$STATE_DIR"
cd "$STATE_DIR"
mkdir -p "${PROJECT_ROOT}/artifacts/logs/legoflow-curator-create"
# Read per-language params from config.yaml
eval $(python "${PROJECT_ROOT}/scripts/read_params.py" --lang js --config-yaml "${PROJECT_ROOT}/config.yaml")
: "${SWE_TASKS_DIR:?read_params.py emitted no SWE_TASKS_DIR — check runtime_info.output.swe_tasks_dir.path in config.yaml}"
# `all` = no cap: legoflow-curator's --max-pr defaults to every entry, so omit the flag.
MAX_PR_ARGS=()
_max_pr="${LEGOFLOW_CURATOR_MAX_PR:-${MAX_VERIFIED_TASKS:-}}"
if [[ -n "$_max_pr" && "$_max_pr" != "all" ]]; then
  MAX_PR_ARGS=(--max-pr "$_max_pr")
fi
echo "TIMEOUT=${TIMEOUT} CC_TIMEOUT=${CC_TIMEOUT} N_CONCURRENT=${N_CONCURRENT}"

# JavaScript tasks are often dependency-heavy and test startup can be slow.
# Use a larger timeout budget while keeping difficulty non-trivial.
# After Feb->March merge, the output's own verifiable_tasks.txt is the single source
# of truth for already successful tasks; external Feb skip files are no longer needed.
legoflow-curator create \
  --input-ids-file "${PROJECT_ROOT}/artifacts/collected_prs/javascript_pr_ids.txt" \
  ${MAX_PR_ARGS[@]+"${MAX_PR_ARGS[@]}"} \
  --n-concurrent "${N_CONCURRENT}" \
  --output "${SWE_TASKS_DIR}/js-cc" \
  --state-dir "$STATE_DIR" \
  --timeout "${TIMEOUT}" \
  --cc-timeout "${CC_TIMEOUT}" \
  --no-require-issue \
  --min-source-files 2 \
  --max-source-files 10 \
  2>&1 | tee "${PROJECT_ROOT}/artifacts/logs/legoflow-curator-create/cc_js_$(date -u +%Y%m%dT%H%M%SZ).txt"
