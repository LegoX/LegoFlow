#!/usr/bin/env bash
# Build an isolated LiteLLM proxy environment for Claude Code translation.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${LITELLM_PROXY_VENV:-$BLOCK_DIR/artifacts/envs/litellm-proxy}"
PYTHON_VERSION="${LITELLM_PROXY_PYTHON:-3.12}"

if command -v uv >/dev/null 2>&1; then
  uv venv "$VENV" --python "$PYTHON_VERSION"
  uv pip install --python "$VENV/bin/python" \
    'litellm[proxy]' 'fastapi==0.140.6'
else
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install 'litellm[proxy]' 'fastapi==0.140.6'
fi

# LiteLLM 1.96 still imports get_flat_dependant; FastAPI 0.140.7 removed it.
"$VENV/bin/python" -c \
  'import backoff, litellm.proxy.proxy_server; from fastapi.dependencies.utils import get_flat_dependant'

echo "LiteLLM proxy environment ready: $VENV"
