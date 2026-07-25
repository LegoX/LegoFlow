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

# --- shared block-contract validation (schema, deps, fill markers) ------------
REPO_ROOT="$(cd ../.. && pwd)"
if [[ -f "$REPO_ROOT/scripts/validate_config.py" ]]; then
  python3 "$REPO_ROOT/scripts/validate_config.py" --block "$(pwd)" \
    || { echo "ERROR: config contract validation failed (see lines above)"; exit 1; }
else
  echo "WARN: shared validator not found — skipping contract validation"
fi

python -c "import swegen; print('swegen: OK')" || { echo "ERROR: run pip install -e repos/swegen/"; exit 1; }
python -c "import yaml; yaml.safe_load(open('config.yaml')); print('config.yaml: OK')"

for var in GITHUB_TOKENS OPENAI_API_KEY OPENAI_API_BASE_URL OPENAI_MODEL ANTHROPIC_MODEL; do
    val="${!var:-}"
    [ -n "$val" ] && echo "${var}: set" || echo "WARN: ${var} not set"
done

# Claude Code path (writes verifiable_tasks.txt). When the provider is OpenAI-only
# (cc_provider_mode=openai_proxy), ANTHROPIC_BASE_URL must point at a running LiteLLM
# proxy; otherwise CC verification fails silently and no tasks are ever verified.
cc_mode="${SWEGEN_CC_PROVIDER_MODE:-}"
echo "cc_provider_mode: ${cc_mode:-unset}"
if [ -n "${ANTHROPIC_BASE_URL:-}" ]; then
    if curl -sf "${ANTHROPIC_BASE_URL%/}/health" >/dev/null 2>&1; then
        echo "CC endpoint (${ANTHROPIC_BASE_URL}): OK"
    elif [ "$cc_mode" = "openai_proxy" ]; then
        echo "FATAL: CC proxy at ${ANTHROPIC_BASE_URL} is down. Start the LiteLLM proxy first" \
             "(see CLAUDE.md 'LLM provider modes'); otherwise CC verification fails silently" \
             "and verifiable_tasks.txt is never written."
    else
        echo "WARN: CC endpoint ${ANTHROPIC_BASE_URL} did not answer /health (native providers may not expose it)."
    fi
else
    echo "WARN: ANTHROPIC_BASE_URL not set; CC verification path is unconfigured."
fi

docker run --rm hello-world >/dev/null 2>&1 && echo "Docker: OK" || echo "WARN: Docker not available"

# Cloudflare Pages (optional): only needed to publish the databoard, i.e. the
# manual `npx wrangler pages deploy` from dashboard/site/ documented in
# dashboard/README.md. Never blocks /curator:run.
CF_ENV_FILE="${ENV_FILE:-${SWEGEN_HOME:-$HOME}/.config/swegen_progress_cloudflare.env}"
CF_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
CF_ACCOUNT="${CLOUDFLARE_ACCOUNT_ID:-}"
if [ -z "$CF_TOKEN" ] && [ -f "$CF_ENV_FILE" ]; then
    CF_TOKEN="$(grep -E '^(export )?CLOUDFLARE_API_TOKEN=' "$CF_ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"' || true)"
    CF_ACCOUNT="$(grep -E '^(export )?CLOUDFLARE_ACCOUNT_ID=' "$CF_ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"' || true)"
fi
if command -v npx >/dev/null 2>&1 && [ -n "$CF_TOKEN" ] && [ -n "$CF_ACCOUNT" ]; then
    echo "cloudflare: OK (npx available, credentials in env or $CF_ENV_FILE)"
else
    missing=()
    command -v npx >/dev/null 2>&1 || missing+=("npx/node")
    [ -n "$CF_TOKEN" ] && [ -n "$CF_ACCOUNT" ] || missing+=("CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID ($CF_ENV_FILE)")
    echo "WARN: cloudflare: missing ${missing[*]} — publishing dashboard/site/ to Cloudflare Pages will fail; the local HTML databoard still works. See /root:setup optional extras."
fi

echo "=== dryrun complete ==="
