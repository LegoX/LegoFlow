#!/bin/bash
set -euo pipefail
cd /root/swegen
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
export DOCKER_BUILDKIT=0
export COMPOSE_DOCKER_CLI_BUILD=0
source .env
# cli.py checks GITHUB_TOKEN (singular); derive from GITHUB_TOKENS if needed
if [ -z "${GITHUB_TOKEN:-}" ] && [ -n "${GITHUB_TOKENS:-}" ]; then
    export GITHUB_TOKEN="${GITHUB_TOKENS%%,*}"
fi
source swegen-env2/bin/activate

mkdir -p artifacts/logs/swegen-create artifacts/swe_tasks/py-cc

exec swegen create \
  --input-ids-file artifacts/collected_prs/python_pr_ids.txt \
  --max-pr 5000 \
  --n-concurrent 20 \
  --output artifacts/swe_tasks/py-cc \
  --timeout 3200 \
  --cc-timeout 2400 \
  --no-require-issue \
  --min-source-files 3 \
  --max-source-files 10 \
  2>&1 | tee artifacts/logs/swegen-create/py_remote.log
