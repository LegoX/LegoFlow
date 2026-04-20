#!/bin/bash
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
source swegen-env2/bin/activate
source scripts/load_runtime_env.sh
echo 'activate swegen-env2'

load_runtime_env

uv pip install -e .

export OPENAI_MODEL="${OPENAI_MODEL:-glm-5-urg}"
export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-sonnet-4-6}"

set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs/swegen-create
# Rust: higher concurrency, sorted by quality
N_CONCURRENT="${N_CONCURRENT:-16}"
echo N_CONCURRENT=${N_CONCURRENT}

# Rust projects need longer build times; use higher cc-timeout
swegen create \
  --input-ids-file "${PROJECT_ROOT}/artifacts/collected_prs/rust_pr_ids.txt" \
  --max-pr 5000 \
  --n-concurrent "${N_CONCURRENT}" \
  --output "${PROJECT_ROOT}/artifacts/swe_tasks/rust-cc" \
  --state-dir .swegen-rust \
  --timeout 3600 \
  --cc-timeout 3000 \
  --no-require-issue \
  --min-source-files 2 \
  --max-source-files 10 \
  2>&1 | tee logs/swegen-create/cc_rust_March.txt
