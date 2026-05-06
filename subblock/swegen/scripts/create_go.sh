#!/bin/bash
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
# python -m venv swegen-env2
source artifacts/envs/swegen-env2/bin/activate  # Linux/Mac
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
eval $(python "${PROJECT_ROOT}/scripts/read_params.py" --lang go --config-yaml "${PROJECT_ROOT}/config.yaml")
echo "TIMEOUT=${TIMEOUT} CC_TIMEOUT=${CC_TIMEOUT} N_CONCURRENT=${N_CONCURRENT}"

# Go projects are usually deterministic, but module download/build can still be expensive.
# After Feb->March merge, the output's own verifiable_tasks.txt is the single source
# of truth for already successful tasks; external Feb skip files are no longer needed.
swegen create \
  --input-ids-file "${PROJECT_ROOT}/artifacts/collected_prs/go_pr_ids.txt" \
  --max-pr 5000 \
  --n-concurrent "${N_CONCURRENT}" \
  --output "${PROJECT_ROOT}/artifacts/swe_tasks/go-cc" \
  --state-dir .swegen-go \
  --timeout "${TIMEOUT}" \
  --cc-timeout "${CC_TIMEOUT}" \
  --no-require-issue \
  --min-source-files 2 \
  --max-source-files 10 \
  2>&1 | tee "${PROJECT_ROOT}/artifacts/logs/swegen-create/cc_go_March.txt"
