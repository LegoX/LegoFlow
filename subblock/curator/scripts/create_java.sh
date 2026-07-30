#!/bin/bash
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
source artifacts/envs/swegen-env/bin/activate
source scripts/load_runtime_env.sh
echo 'activate swegen-env'

load_runtime_env

python -c 'import swegen' 2>/dev/null || pip install -e repos/swegen/  # standalone fallback; create_all_bg.sh pre-installs once

# OPENAI_MODEL / ANTHROPIC_MODEL come from config.yaml via load_runtime_env.

set -euo pipefail

# swegen writes its state dir AND any stray tool output into the CWD;
# keep both under artifacts/ instead of polluting scripts/.
STATE_DIR="${PROJECT_ROOT}/artifacts/state/swegen-java"
mkdir -p "$STATE_DIR"
cd "$STATE_DIR"
mkdir -p "${PROJECT_ROOT}/artifacts/logs/swegen-create"
# Read per-language params from config.yaml
eval $(python "${PROJECT_ROOT}/scripts/read_params.py" --lang java --config-yaml "${PROJECT_ROOT}/config.yaml")
echo "TIMEOUT=${TIMEOUT} CC_TIMEOUT=${CC_TIMEOUT} N_CONCURRENT=${N_CONCURRENT}"

swegen create \
  --input-ids-file "${PROJECT_ROOT}/artifacts/collected_prs/java_pr_ids.txt" \
  --max-pr "${SWEGEN_MAX_PR:-${MAX_VERIFIED_TASKS:-5000}}" \
  --n-concurrent "${N_CONCURRENT}" \
  --output "${PROJECT_ROOT}/artifacts/swe_tasks/java-cc" \
  --state-dir "$STATE_DIR" \
  --timeout "${TIMEOUT}" \
  --cc-timeout "${CC_TIMEOUT}" \
  --no-require-issue \
  --min-source-files 2 \
  --max-source-files 10 \
  2>&1 | tee "${PROJECT_ROOT}/artifacts/logs/swegen-create/cc_java_$(date -u +%Y%m%dT%H%M%SZ).txt"
