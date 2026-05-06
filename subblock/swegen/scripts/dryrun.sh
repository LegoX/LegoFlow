#!/bin/bash
# Validate environment, package, and inputs before running.
cd "$(dirname "$0")/.."
set -euo pipefail
source scripts/load_runtime_env.sh
load_runtime_env

echo "=== swegen dryrun ==="

python -c "import swegen; print('swegen: OK')" || { echo "ERROR: run pip install -e repos/swegen/"; exit 1; }
python -c "import yaml; yaml.safe_load(open('config.yaml')); print('config.yaml: OK')"

for var in GITHUB_TOKENS OPENAI_API_KEY OPENAI_API_BASE_URL OPENAI_MODEL ANTHROPIC_MODEL; do
    val="${!var:-}"
    [ -n "$val" ] && echo "${var}: set" || echo "WARN: ${var} not set"
done

docker run --rm hello-world >/dev/null 2>&1 && echo "Docker: OK" || echo "WARN: Docker not available"

echo "=== dryrun complete ==="
