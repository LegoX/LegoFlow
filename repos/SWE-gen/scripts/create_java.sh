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
# Java: higher concurrency (Maven/Gradle builds are slow but independent)
N_CONCURRENT="${N_CONCURRENT:-20}"
echo N_CONCURRENT=${N_CONCURRENT}

swegen create \
  --input-ids-file "${PROJECT_ROOT}/artifacts/collected_prs/java_pr_ids.txt" \
  --max-pr 5000 \
  --n-concurrent "${N_CONCURRENT}" \
  --output "${PROJECT_ROOT}/artifacts/swe_tasks/java-cc" \
  --state-dir .swegen-java \
  --timeout 3200 \
  --cc-timeout 2400 \
  --no-require-issue \
  --min-source-files 2 \
  --max-source-files 10 \
  2>&1 | tee logs/swegen-create/cc_java_March.txt
