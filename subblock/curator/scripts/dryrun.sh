#!/bin/bash
# Validate environment, package, and inputs before running.
cd "$(dirname "$0")/.."
set -euo pipefail

# Activate the swegen venv so `python` and `swegen` resolve to the editable install.
if [[ -f artifacts/envs/swegen-env/bin/activate ]]; then
    # shellcheck disable=SC1091
    source artifacts/envs/swegen-env/bin/activate
else
    echo "ERROR: venv at artifacts/envs/swegen-env not found — run /curator:setup first" >&2
    exit 1
fi

source scripts/load_runtime_env.sh
load_runtime_env

echo "=== curator dryrun ==="

python -c "import swegen; print('swegen: OK')" || { echo "ERROR: run pip install -e repos/swegen/"; exit 1; }
python -c "import yaml; yaml.safe_load(open('config.yaml')); print('config.yaml: OK')"

for var in GITHUB_TOKENS OPENAI_API_KEY OPENAI_API_BASE_URL OPENAI_MODEL ANTHROPIC_MODEL; do
    val="${!var:-}"
    [ -n "$val" ] && echo "${var}: set" || echo "WARN: ${var} not set"
done

docker run --rm hello-world >/dev/null 2>&1 && echo "Docker: OK" || echo "WARN: Docker not available"

echo "=== dryrun complete ==="
