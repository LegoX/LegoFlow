#!/bin/bash
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
# python -m venv swegen-env2
source swegen-env2/bin/activate  # Linux/Mac
source scripts/load_runtime_env.sh
echo 'activate swegen-env2'

load_runtime_env

uv pip install -e .

export OPENAI_MODEL="${OPENAI_MODEL:-glm-5-urg}"
export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-sonnet-4-6}"

set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs/swegen-create
N_CONCURRENT="${N_CONCURRENT:-16}"
echo N_CONCURRENT=${N_CONCURRENT}

# Align Python with Go/JS thresholds/timeouts to avoid over-filtering and premature CC timeout.
# After Feb->March merge, the output's own verifiable_tasks.txt is the single source
# of truth for already successful tasks; external Feb skip files are no longer needed.
swegen create \
  --input-ids-file "${PROJECT_ROOT}/artifacts/collected_prs/python_pr_ids.txt" \
  --max-pr 5000 \
  --n-concurrent "${N_CONCURRENT}" \
  --output "${PROJECT_ROOT}/artifacts/swe_tasks/py-cc" \
  --state-dir .swegen-py \
  --timeout 3200 \
  --cc-timeout 2400 \
  --no-require-issue \
  --min-source-files 3 \
  --max-source-files 10 \
  2>&1 | tee logs/swegen-create/cc_py_March.txt
