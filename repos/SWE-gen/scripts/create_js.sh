#!/bin/bash
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
# python -m venv swegen-env2
source swegen-env2/bin/activate  # Linux/Mac
source scripts/load_runtime_env.sh
echo 'activate swegen-env2'

load_runtime_env

uv pip install -e .

# MiniMax-M2.5
# claude-sonnet-4-6
# glm-5
export OPENAI_MODEL="${OPENAI_MODEL:-glm-5-urg}"
export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-sonnet-4-6}"

set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs/swegen-create
N_CONCURRENT="${N_CONCURRENT:-16}"
echo N_CONCURRENT=${N_CONCURRENT}

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
  --timeout 3200 \
  --cc-timeout 2400 \
  --no-require-issue \
  --min-source-files 2 \
  --max-source-files 10 \
  2>&1 | tee logs/swegen-create/cc_js_March.txt


###[Q] resume的时候，如果我修改了pr筛选的条件，比如-min-source-files 2改成-min-source-files 1，那么之前因为-min-source-files =1的而未处理的pr是否会在resume的时候被处理

# [A] 不会，按当前逻辑 不会自动处理 这类 PR。
# 你现在可用的做法
# 最简单：删该输出目录下对应的 batch 状态文件后重跑
# tasks/March/js-wo-cc/.swegen-create-batch/<hash>.json
# 或者换一个新的 --output 目录（等价于新状态）
# 这样会按新筛选条件重新评估（同时仍会基于 verifiable_tasks.txt 保留已成功任务）。
