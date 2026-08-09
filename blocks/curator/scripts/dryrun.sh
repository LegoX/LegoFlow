#!/bin/bash
# Validate environment, package, and inputs before running.
cd "$(dirname "$0")/.."
set -euo pipefail

# Activate the legoflow-curator venv so `python` and `legoflow-curator` resolve to the editable install.
if [[ -f artifacts/envs/legoflow-curator-env/bin/activate ]]; then
    # shellcheck disable=SC1091
    source artifacts/envs/legoflow-curator-env/bin/activate
else
    echo "ERROR: venv at artifacts/envs/legoflow-curator-env not found — run /curator:setup first" >&2
    exit 1
fi

source scripts/load_runtime_env.sh
load_runtime_env

echo "=== curator dryrun ==="

# --- shared block-contract validation (schema, deps, fill markers) ------------
REPO_ROOT="$(cd ../.. && pwd)"
if [[ -f "$REPO_ROOT/scripts/validate_config.py" ]]; then
  # Tracked configs intentionally retain `human` markers; runtime credentials
  # are supplied through the environment and checked explicitly below.
  python3 "$REPO_ROOT/scripts/validate_config.py" --block "$(pwd)" --schema-only \
    || { echo "ERROR: config contract validation failed (see lines above)"; exit 1; }
else
  echo "WARN: shared validator not found — skipping contract validation"
fi

python -c "import legoflow_curator; print('legoflow-curator: OK')" || { echo "ERROR: run pip install -e repos/legoflow-curator/"; exit 1; }
python -c "import yaml; yaml.safe_load(open('config.yaml')); print('config.yaml: OK')"

missing_runtime=0
for var in GITHUB_TOKENS OPENAI_API_KEY OPENAI_API_BASE_URL OPENAI_MODEL ANTHROPIC_MODEL; do
    val="${!var:-}"
    if [ -n "$val" ]; then
        echo "${var}: set"
    else
        echo "ERROR: ${var} not set" >&2
        missing_runtime=1
    fi
done
[[ "$missing_runtime" -eq 0 ]] || exit 1

# Claude Code path (writes verifiable_tasks.txt). When the provider is OpenAI-only
# (cc_provider_mode=openai_proxy), ANTHROPIC_BASE_URL must point at a running LiteLLM
# proxy; otherwise CC verification fails silently and no tasks are ever verified.
cc_mode="${LEGOFLOW_CURATOR_CC_PROVIDER_MODE:-}"
echo "cc_provider_mode: ${cc_mode:-unset}"
if [ -n "${ANTHROPIC_BASE_URL:-}" ]; then
    # /health probes every configured model (~20s under load); fall back to it
    # only for native providers, which do not expose the liveliness route.
    if curl -sf --max-time 10 "${ANTHROPIC_BASE_URL%/}/health/liveliness" >/dev/null 2>&1 \
       || curl -sf --max-time 30 "${ANTHROPIC_BASE_URL%/}/health" >/dev/null 2>&1; then
        echo "CC endpoint (${ANTHROPIC_BASE_URL}): OK"
    elif [ "$cc_mode" = "openai_proxy" ]; then
        echo "FATAL: CC proxy at ${ANTHROPIC_BASE_URL} is down. Start the LiteLLM proxy first" \
             "(see CLAUDE.md 'LLM provider modes'); otherwise CC verification fails silently" \
             "and verifiable_tasks.txt is never written."
    else
        echo "WARN: CC endpoint ${ANTHROPIC_BASE_URL} did not answer /health/liveliness or /health (native providers may not expose either)."
    fi
else
    echo "WARN: ANTHROPIC_BASE_URL not set; CC verification path is unconfigured."
fi

docker run --rm hello-world >/dev/null 2>&1 && echo "Docker: OK" || echo "WARN: Docker not available"

# Optional shared credentials (Cloudflare Pages publishing + authenticated image
# pulls). Resolved by scripts/shared_credentials.sh in this order: env > root
# config.yaml (runtime_info.input.cloudflare/docker) > this block's legacy env
# file. Never blocks /curator:run.
CF_ENV_FILE="${ENV_FILE:-${LEGOFLOW_CURATOR_HOME:-$HOME}/.config/legoflow_curator_dashboard_cloudflare.env}"
if [ -f "$REPO_ROOT/scripts/shared_credentials.sh" ]; then
    CF_LEGACY_ENV_FILE="$CF_ENV_FILE"
    # shellcheck source=/dev/null
    source "$REPO_ROOT/scripts/shared_credentials.sh"
    load_shared_credentials "$(pwd)"
else
    echo "WARN: scripts/shared_credentials.sh not found at repo root — using env vars only"
fi

if command -v npx >/dev/null 2>&1 && [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
    echo "cloudflare: OK (npx available, credentials from ${SHARED_CLOUDFLARE_SOURCE:-env})"
else
    missing=()
    command -v npx >/dev/null 2>&1 || missing+=("npx/node")
    [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] || \
        missing+=("CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID (root config.yaml runtime_info.input.cloudflare, env, or $CF_ENV_FILE)")
    echo "WARN: cloudflare: missing ${missing[*]} — publishing dashboard/site/ to Cloudflare Pages will fail; the local HTML databoard still works. See /root:setup optional extras."
fi

# Registry auth raises the anonymous 100-pulls-per-6h-per-IP cap that otherwise
# breaks task image pulls partway through a long create run.
if [ -n "${DOCKER_USERNAME:-}" ] && [ -n "${DOCKER_PASSWORD:-}" ]; then
    echo "docker registry: OK (credentials from ${SHARED_DOCKER_SOURCE:-env}; run scripts/docker_login.sh to authenticate pulls)"
else
    echo "WARN: docker registry: no credentials (root config.yaml runtime_info.input.docker or DOCKER_USERNAME/DOCKER_PASSWORD) — pulls stay anonymous and capped at 100/6h per IP"
fi

echo "=== dryrun complete ==="
