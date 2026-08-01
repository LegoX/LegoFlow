#!/bin/bash
# Launch swegen for the NATIVE Claude Code provider mode.
#
# Use this when config.yaml -> runtime_info.input.llm_api.cc_provider_mode is
# "native": your provider already speaks the Anthropic Messages API
# (real Claude, or a gateway exposing /v1/messages directly). No local proxy
# is started; ANTHROPIC_BASE_URL points straight at the provider.
#
# For OpenAI-only providers (Qwen / GLM / sglang / vLLM), use the sibling
# launcher start_with_openai_api.sh instead.
#
# Pipeline: create_all_bg (8 langs in background) -> archive launcher exit.
set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BLOCK_DIR"

# Read cc_provider_mode from config.yaml and refuse to run in the wrong mode.
PY_BIN="${PY_BIN:-artifacts/envs/swegen-env/bin/python}"
[ -x "$PY_BIN" ] || PY_BIN=python3
MODE="$("$PY_BIN" -c "import yaml,sys;c=yaml.safe_load(open('config.yaml'));print((c.get('runtime_info',{}).get('input',{}).get('llm_api',{}) or {}).get('cc_provider_mode',''))")"
if [ "$MODE" != "native" ]; then
  echo "ERROR: this launcher requires llm_api.cc_provider_mode == 'native'." >&2
  echo "  config.yaml has: cc_provider_mode = '${MODE}'." >&2
  echo "  For openai_proxy mode use: bash scripts/start_with_openai_api.sh" >&2
  exit 2
fi

echo "[start_with_anthropic_api] cc_provider_mode=native; no local proxy needed."
# Guard so start.sh runs generation directly instead of dispatching back here.
export SWEGEN_LAUNCHER_ACTIVE=1
exec bash "${BLOCK_DIR}/scripts/start.sh" "$@"
