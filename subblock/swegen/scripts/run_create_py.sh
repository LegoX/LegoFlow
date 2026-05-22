#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
export DOCKER_BUILDKIT=0
export COMPOSE_DOCKER_CLI_BUILD=0
source .env
source scripts/load_runtime_env.sh
load_runtime_env
source swegen-env2/bin/activate

exec swegen-env2/bin/swegen create \
  --input-ids-file artifacts/collected_prs/python_pr_ids.txt \
  --max-pr 5000 \
  --n-concurrent 20 \
  --output artifacts/swe_tasks/py-cc \
  --timeout 3200 \
  --cc-timeout 2400 \
  --no-require-issue \
  --min-source-files 3 \
  --max-source-files 10
