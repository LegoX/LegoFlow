#!/bin/bash
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
# python -m venv swegen-env
source artifacts/envs/swegen-env/bin/activate  # Linux/Mac
source scripts/load_runtime_env.sh
echo 'activate swegen-env'

load_runtime_env

pip install -e repos/swegen/

# MiniMax-M2.5
# claude-sonnet-4-6
# glm-5
export OPENAI_MODEL="${OPENAI_MODEL:-glm-5-urg}"
export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-sonnet-4-6}"

set -euo pipefail

cd "$(dirname "$0")"
mkdir -p "${PROJECT_ROOT}/artifacts/logs/swegen-create"
# Read params from inputs.yaml (adaptive tuning)
eval $(python "${PROJECT_ROOT}/scripts/read_params.py" --lang js --config-yaml "${PROJECT_ROOT}/config.yaml")
echo "TIMEOUT=${TIMEOUT} CC_TIMEOUT=${CC_TIMEOUT} N_CONCURRENT=${N_CONCURRENT}"

# JavaScript tasks are often dependency-heavy and test startup can be slow.
# Use a larger timeout budget while keeping difficulty non-trivial.
# After Feb->March merge, the output's own verifiable_tasks.txt is the single source
# of truth for already successful tasks; external Feb skip files are no longer needed.
swegen create \
  --input-ids-file "${PROJECT_ROOT}/artifacts/collected_prs/javascript_pr_ids.txt" \
  --max-pr 5000 \
  --n-concurrent "${N_CONCURRENT}" \
  --output "${PROJECT_ROOT}/artifacts/swe_tasks/js-cc" \
  --state-dir .swegen-js \
  --timeout "${TIMEOUT}" \
  --cc-timeout "${CC_TIMEOUT}" \
  --no-require-issue \
  --min-source-files 2 \
  --max-source-files 10 \
  2>&1 | tee "${PROJECT_ROOT}/artifacts/logs/swegen-create/cc_js_March.txt"
