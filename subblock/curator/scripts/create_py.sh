#!/bin/bash
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
# python -m venv swegen-env
source artifacts/envs/swegen-env/bin/activate  # Linux/Mac
source scripts/load_runtime_env.sh
echo 'activate swegen-env'

load_runtime_env

python -c 'import swegen' 2>/dev/null || pip install -e repos/swegen/  # standalone fallback; create_all_bg.sh pre-installs once

# OPENAI_MODEL / ANTHROPIC_MODEL come from config.yaml via load_runtime_env.

set -euo pipefail

# swegen writes its state dir AND any stray tool output into the CWD;
# keep both under artifacts/ instead of polluting scripts/.
STATE_DIR="${PROJECT_ROOT}/artifacts/state/swegen-py"
mkdir -p "$STATE_DIR"
cd "$STATE_DIR"
mkdir -p "${PROJECT_ROOT}/artifacts/logs/swegen-create"
# Read per-language params from config.yaml
eval $(python "${PROJECT_ROOT}/scripts/read_params.py" --lang py --config-yaml "${PROJECT_ROOT}/config.yaml")
: "${SWE_TASKS_DIR:?read_params.py emitted no SWE_TASKS_DIR — check runtime_info.output.swe_tasks_dir.path in config.yaml}"
echo "TIMEOUT=${TIMEOUT} CC_TIMEOUT=${CC_TIMEOUT} N_CONCURRENT=${N_CONCURRENT}"

# Align Python with the other languages' thresholds/timeouts to avoid over-filtering
# and premature CC timeout. min-source-files=2 matches create_{js,ts,go,...}.sh and the
# CLAUDE.md example; lower it to 1 (e.g. via a smoke run) when you want maximum yield.
# After Feb->March merge, the output's own verifiable_tasks.txt is the single source
# of truth for already successful tasks; external Feb skip files are no longer needed.
swegen create \
  --input-ids-file "${PROJECT_ROOT}/artifacts/collected_prs/python_pr_ids.txt" \
  --max-pr "${SWEGEN_MAX_PR:-${MAX_VERIFIED_TASKS:-5000}}" \
  --n-concurrent "${N_CONCURRENT}" \
  --output "${SWE_TASKS_DIR}/py-cc" \
  --state-dir "$STATE_DIR" \
  --timeout "${TIMEOUT}" \
  --cc-timeout "${CC_TIMEOUT}" \
  --no-require-issue \
  --min-source-files 2 \
  --max-source-files 10 \
  2>&1 | tee "${PROJECT_ROOT}/artifacts/logs/swegen-create/cc_py_$(date -u +%Y%m%dT%H%M%SZ).txt"
