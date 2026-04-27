#!/bin/bash
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
source swegen-env2/bin/activate
source scripts/load_runtime_env.sh
echo 'activate swegen-env2'

load_runtime_env

uv pip install -e repos/swegen/

export OPENAI_MODEL="${OPENAI_MODEL:-glm-5-urg}"
export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-sonnet-4-6}"

set -euo pipefail

cd "$(dirname "$0")"
mkdir -p "${PROJECT_ROOT}/artifacts/logs/swegen-create"
# Read params from inputs.yaml (adaptive tuning)
eval $(python "${PROJECT_ROOT}/scripts/read_params.py" --lang c --inputs-yaml "${PROJECT_ROOT}/inputs.yaml")
echo "TIMEOUT=${TIMEOUT} CC_TIMEOUT=${CC_TIMEOUT} N_CONCURRENT=${N_CONCURRENT}"

swegen create \
  --input-ids-file "${PROJECT_ROOT}/artifacts/collected_prs/c_pr_ids.txt" \
  --max-pr 5000 \
  --n-concurrent "${N_CONCURRENT}" \
  --output "${PROJECT_ROOT}/artifacts/swe_tasks/c-cc" \
  --state-dir .swegen-c \
  --timeout "${TIMEOUT}" \
  --cc-timeout "${CC_TIMEOUT}" \
  --no-require-issue \
  --min-source-files 2 \
  --max-source-files 10 \
  2>&1 | tee "${PROJECT_ROOT}/artifacts/logs/swegen-create/cc_c_March.txt"
