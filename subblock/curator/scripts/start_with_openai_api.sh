#!/bin/bash
# Launch swegen for the OPENAI_PROXY Claude Code provider mode.
#
# Use this when config.yaml -> runtime_info.input.llm_api.cc_provider_mode is
# "openai_proxy": your provider is OpenAI-only (Qwen / GLM / sglang / vLLM /
# most self-hosted endpoints). These reject role:system on Anthropic /v1/messages
# with HTTP 400, so we run a local LiteLLM proxy that translates
# Anthropic -> OpenAI. anthropic_base_url in config.yaml must point at this
# local proxy URL (e.g. http://127.0.0.1:4010).
#
# For real Claude or an Anthropic-format gateway, use the sibling launcher
# start_with_anthropic_api.sh instead.
#
# Pipeline: read config -> start LiteLLM proxy -> wait for /health -> dryrun
#           -> create_all_bg -> archive on exit -> stop proxy.
set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BLOCK_DIR"

PY_BIN="${PY_BIN:-artifacts/envs/swegen-env/bin/python}"
[ -x "$PY_BIN" ] || PY_BIN=python3
read MODE PORT < <("$PY_BIN" -c "
import yaml,sys
c=yaml.safe_load(open('config.yaml'))
llm=(c.get('runtime_info',{}).get('input',{}).get('llm_api',{}) or {})
print(llm.get('cc_provider_mode',''), llm.get('cc_proxy_port',4010))
")
if [ "$MODE" != "openai_proxy" ]; then
  echo "ERROR: this launcher requires llm_api.cc_provider_mode == 'openai_proxy'." >&2
  echo "  config.yaml has: cc_provider_mode = '${MODE}'." >&2
  echo "  For native mode use: bash scripts/start_with_anthropic_api.sh" >&2
  exit 2
fi

PROXY_CONFIG="${BLOCK_DIR}/scripts/litellm_cc_proxy.example.yaml"
PROXY_LOG_DIR="${BLOCK_DIR}/artifacts/logs"
mkdir -p "$PROXY_LOG_DIR"
PROXY_LOG="${PROXY_LOG_DIR}/litellm_cc_proxy.log"

# Resolve litellm binary. The block's swegen-env may not ship litellm; tracer
# block's litellm-venv is the canonical location. Override via LITELLM_BIN.
LITELLM_BIN="${LITELLM_BIN:-}"
if [ -z "$LITELLM_BIN" ]; then
  for cand in \
    "${BLOCK_DIR}/artifacts/envs/swegen-env/bin/litellm" \
    "${BLOCK_DIR}/../tracer/artifacts/env/litellm-venv/bin/litellm" \
    "$(command -v litellm 2>/dev/null || true)"; do
    [ -n "$cand" ] && [ -x "$cand" ] && { LITELLM_BIN="$cand"; break; }
  done
fi
if [ -z "$LITELLM_BIN" ] || [ ! -x "$LITELLM_BIN" ]; then
  echo "ERROR: cannot find a litellm binary. Set LITELLM_BIN=/path/to/litellm." >&2
  exit 3
fi

# Refuse to start if the proxy_config still has unfilled placeholders.
if grep -qE "<UPSTREAM_OPENAI_BASE_URL>|<UPSTREAM_MODEL>|<API_KEY>" "$PROXY_CONFIG"; then
  echo "ERROR: $PROXY_CONFIG still has placeholder values." >&2
  echo "  Fill in <UPSTREAM_OPENAI_BASE_URL>, <UPSTREAM_MODEL>, <API_KEY> first." >&2
  exit 4
fi

# Free the port if a stale proxy holds it.
if lsof -ti:"${PORT}" >/dev/null 2>&1; then
  echo "[start_with_openai_api] port ${PORT} busy; killing previous holder."
  lsof -ti:"${PORT}" | xargs -r kill 2>/dev/null || true
  sleep 1
fi

echo "[start_with_openai_api] starting LiteLLM proxy on :${PORT} (log: ${PROXY_LOG})"
env -i HOME="$HOME" PATH="$PATH" LANG="${LANG:-C.UTF-8}" HOST=0.0.0.0 \
  "$LITELLM_BIN" --config "$PROXY_CONFIG" --port "${PORT}" --host 127.0.0.1 \
  >"$PROXY_LOG" 2>&1 &
LITELLM_PID=$!
echo "[start_with_openai_api] litellm PID=${LITELLM_PID}"

cleanup_proxy() {
  if [ -n "${LITELLM_PID:-}" ] && kill -0 "$LITELLM_PID" 2>/dev/null; then
    echo "[start_with_openai_api] stopping LiteLLM proxy (PID=${LITELLM_PID})"
    kill "$LITELLM_PID" 2>/dev/null || true
    wait "$LITELLM_PID" 2>/dev/null || true
  fi
}
trap cleanup_proxy EXIT INT TERM

# Wait up to 90s for /health.
waited=0
until curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; do
  sleep 2; waited=$((waited+2))
  if [ $waited -ge 90 ]; then
    echo "ERROR: LiteLLM proxy did not become ready within 90s." >&2
    tail -30 "$PROXY_LOG" >&2 || true
    exit 5
  fi
done
echo "[start_with_openai_api] LiteLLM proxy ready on :${PORT}."

# Delegate to the shared start.sh (which runs archive + create_all_bg).
bash "${BLOCK_DIR}/scripts/start.sh" "$@"
