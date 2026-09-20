#!/bin/bash
# Launch legoflow-curator for the OPENAI_PROXY Claude Code provider mode.
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
# Pipeline: read config -> reuse or start LiteLLM proxy -> wait for /health
#           -> create_all_bg -> archive launcher exit.
#
# NOTE on proxy lifecycle: create_all_bg.sh launches all 8 per-language
# `legoflow-curator create` jobs detached (nohup) and returns almost immediately, but
# those jobs keep needing the CC proxy for hours afterward. This script
# therefore does NOT kill a proxy it started when it exits (that used to
# happen via an EXIT trap and silently broke verification for every
# in-flight job the moment this launcher returned). If this script started
# the proxy, it's left running; see PROXY_PID_FILE below for how to stop it
# once every language job has finished.
set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BLOCK_DIR"

# --proxy-only: used by /curator:setup to start (or reuse) the CC proxy
# without launching create_all_bg; strip it before the final "$@" forward.
PROXY_ONLY=0
args=()
for a in "$@"; do
  if [ "$a" = "--proxy-only" ]; then PROXY_ONLY=1; else args+=("$a"); fi
done
set -- "${args[@]}"

PY_BIN="${PY_BIN:-artifacts/envs/legoflow-curator-env/bin/python}"
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

# Prefer the real, filled config over the placeholder example. The example
# is a template only -- pointing this launcher at it directly (the old
# default) meant it always tripped the placeholder check below.
PROXY_CONFIG="${BLOCK_DIR}/scripts/litellm_cc_proxy.yaml"
if [ ! -f "$PROXY_CONFIG" ]; then
  echo "ERROR: ${PROXY_CONFIG} does not exist yet." >&2
  echo "  Create it from the template and fill in the three placeholders:" >&2
  echo "    cp scripts/litellm_cc_proxy.example.yaml scripts/litellm_cc_proxy.yaml" >&2
  echo "  then edit <UPSTREAM_OPENAI_BASE_URL>, <UPSTREAM_MODEL>, <API_KEY> using" >&2
  echo "  llm_api.api_base_url / llm_api.pr_model / llm_api.api_key from config.yaml" >&2
  echo "  (the CC path maps claude-* aliases to pr_model, not task_model)." >&2
  exit 4
fi
PROXY_LOG_DIR="${BLOCK_DIR}/artifacts/logs"
mkdir -p "$PROXY_LOG_DIR"
PROXY_LOG="${PROXY_LOG_DIR}/litellm_cc_proxy.log"
PROXY_PID_FILE="${PROXY_LOG_DIR}/litellm_cc_proxy.pid"

# Resolve litellm binary. The block's legoflow-curator-env may not ship litellm; tracer
# block's litellm-venv is the canonical location. Override via LITELLM_BIN.
LITELLM_BIN="${LITELLM_BIN:-}"
if [ -z "$LITELLM_BIN" ]; then
  for cand in \
    "${BLOCK_DIR}/../tracer/artifacts/env/litellm-venv/bin/litellm" \
    "${BLOCK_DIR}/artifacts/envs/legoflow-curator-env/bin/litellm" \
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

# Reuse an already-healthy proxy on this port instead of always killing and
# restarting -- a long-running curator session may already have one up (e.g.
# started manually, or by a previous invocation of this launcher whose
# create_all_bg workers are still in flight and still depend on it).
STARTED_PROXY=0
# liveliness, not /health: /health takes ~20s under load, and timing out here
# would drop into the else-branch and kill a proxy the workers still need.
if curl -sf --max-time 10 "http://127.0.0.1:${PORT}/health/liveliness" >/dev/null 2>&1; then
  echo "[start_with_openai_api] reusing already-healthy LiteLLM proxy on :${PORT}."
else
  # Free the port if a stale (unhealthy) holder is squatting on it.
  if lsof -ti:"${PORT}" >/dev/null 2>&1; then
    echo "[start_with_openai_api] port ${PORT} busy but not healthy; killing previous holder."
    lsof -ti:"${PORT}" | xargs -r kill 2>/dev/null || true
    sleep 1
  fi

  echo "[start_with_openai_api] starting LiteLLM proxy on :${PORT} (log: ${PROXY_LOG})"
  env -i HOME="$HOME" PATH="$PATH" LANG="${LANG:-C.UTF-8}" HOST=0.0.0.0 \
    nohup "$LITELLM_BIN" --config "$PROXY_CONFIG" --port "${PORT}" --host 127.0.0.1 \
    >"$PROXY_LOG" 2>&1 &
  LITELLM_PID=$!
  disown
  STARTED_PROXY=1
  echo "$LITELLM_PID" > "$PROXY_PID_FILE"
  echo "[start_with_openai_api] litellm PID=${LITELLM_PID} (written to ${PROXY_PID_FILE})"

  # Only clean up on a FAILED startup here, before any create_all_bg worker
  # exists to depend on the proxy. Once /health succeeds below, the trap is
  # cleared -- the proxy must outlive this launcher for the detached workers.
  cleanup_proxy_on_startup_failure() {
    if [ "$STARTED_PROXY" -eq 1 ] && kill -0 "$LITELLM_PID" 2>/dev/null; then
      echo "[start_with_openai_api] startup failed; stopping LiteLLM proxy (PID=${LITELLM_PID})"
      kill "$LITELLM_PID" 2>/dev/null || true
      wait "$LITELLM_PID" 2>/dev/null || true
      rm -f "$PROXY_PID_FILE"
    fi
  }
  trap cleanup_proxy_on_startup_failure EXIT INT TERM
fi

# Wait up to 90s for liveliness.
waited=0
until curl -sf --max-time 10 "http://127.0.0.1:${PORT}/health/liveliness" >/dev/null 2>&1; do
  sleep 2; waited=$((waited+2))
  if [ $waited -ge 90 ]; then
    echo "ERROR: LiteLLM proxy did not become ready within 90s." >&2
    tail -30 "$PROXY_LOG" >&2 || true
    exit 5
  fi
done
echo "[start_with_openai_api] LiteLLM proxy is up on :${PORT}."

# /health/liveliness only proves the process is up — it checks no dependencies,
# so a wrong upstream model or key still answers 200. Without a routing check
# the launcher would hand a dead proxy to every detached worker, and CC
# verification fails silently for the whole run. Probe the Anthropic path the
# Claude Code SDK actually uses, and refuse to launch workers if it has no text.
CC_TASK_MODEL="$("$PY_BIN" -c "
import yaml
c=yaml.safe_load(open('config.yaml'))
print((c.get('runtime_info',{}).get('input',{}).get('llm_api',{}) or {}).get('task_model',''))
")"
CC_API_KEY="$("$PY_BIN" -c "
import yaml
c=yaml.safe_load(open('config.yaml'))
print((c.get('runtime_info',{}).get('input',{}).get('llm_api',{}) or {}).get('api_key',''))
")"
PROBE="$(cd "$BLOCK_DIR/../.." && pwd)/scripts/probe_llm_endpoint.py"
if [ -f "$PROBE" ]; then
  if python3 "$PROBE" --anthropic-only --attempts 3 \
       --label "CC proxy model routing" \
       --base-url "http://127.0.0.1:${PORT}" \
       --anthropic-base-url "http://127.0.0.1:${PORT}" \
       --anthropic-model "$CC_TASK_MODEL" \
       --api-key "$CC_API_KEY"; then
    echo "[start_with_openai_api] CC proxy routes ${CC_TASK_MODEL} to the upstream."
  else
    echo "ERROR: the proxy is up but cannot complete a request for '${CC_TASK_MODEL}'." >&2
    echo "  Workers would burn their whole run on failing agent calls; not launching." >&2
    tail -30 "$PROXY_LOG" >&2 || true
    exit 6
  fi
fi

# Startup succeeded (or we reused an existing proxy) -- disarm the
# startup-failure trap so a healthy proxy is never killed just because this
# launcher exits. The detached create_all_bg workers depend on it for hours.
if [ "$STARTED_PROXY" -eq 1 ]; then
  trap - EXIT INT TERM
  echo "[start_with_openai_api] proxy left running independently of this launcher (PID=${LITELLM_PID})."
  echo "  Stop it manually once every language job finishes: kill \$(cat ${PROXY_PID_FILE})"
fi

if [ "$PROXY_ONLY" -eq 1 ]; then
  exit 0
fi

# Delegate to the shared start.sh (which runs archive + create_all_bg).
# Guard so start.sh runs generation directly instead of dispatching back here.
export LEGOFLOW_CURATOR_LAUNCHER_ACTIVE=1
bash "${BLOCK_DIR}/scripts/start.sh" "$@"
