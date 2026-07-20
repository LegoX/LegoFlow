#!/usr/bin/env bash
# Shared helper: start/stop a local LiteLLM proxy for curator's Claude Code (CC)
# verification path when llm_api.cc_provider_mode == openai_proxy.
#
# Why this exists separately from scripts/start_with_openai_api.sh: that launcher
# starts the proxy and then delegates to start.sh (all-language generation; PR
# collection is separate).
# The smoke drivers (.github/scripts/smoke_run.sh and tests/smoke/10_pr_demo.sh)
# build their own narrow `swegen create` command, so they must own the proxy
# lifecycle themselves. This lib gives them the same proxy-start logic without
# duplicating it.
#
# Usage:
#   source scripts/cc_proxy_lib.sh
#   cc_proxy_start "$BLOCK_DIR" "$BLOCK_DIR/config.yaml"   # no-op unless openai_proxy
#   trap cc_proxy_stop EXIT
#
# cc_proxy_start returns 0 (no-op) when cc_provider_mode != openai_proxy, so the
# caller can invoke it unconditionally. It returns non-zero only on a real
# failure (missing config values, no litellm binary, proxy never healthy).

CC_PROXY_PID=""

# _cc_cfg <config.yaml> <llm_api-key>  → prints the value (empty if absent).
_cc_cfg() {
  python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import sys, yaml
try:
    d = yaml.safe_load(open(sys.argv[1])) or {}
except Exception:
    sys.exit(0)
llm = (((d.get("runtime_info") or {}).get("input") or {}).get("llm_api") or {})
v = llm.get(sys.argv[2])
print("" if v is None else v)
PY
}

# _cc_litellm_proxy_ok <litellm-bin>  → 0 iff its interpreter can serve the
# proxy (i.e. the litellm[proxy] extra, e.g. `backoff`, is importable).
_cc_litellm_proxy_ok() {
  local py
  py="$(dirname "$1")/python"
  [ -x "$py" ] || py="$(dirname "$1")/python3"
  [ -x "$py" ] || return 1
  "$py" -c 'import backoff, litellm.proxy.proxy_server' >/dev/null 2>&1
}

cc_proxy_start() {
  local block_dir="$1" cfg="$2"
  local mode port base model key

  mode="$(_cc_cfg "$cfg" cc_provider_mode)"
  if [ "$mode" != "openai_proxy" ]; then
    echo "INFO: cc_provider_mode='${mode:-unset}' — no LiteLLM CC proxy needed."
    return 0
  fi

  port="$(_cc_cfg "$cfg" cc_proxy_port)"; port="${port:-4010}"
  base="$(_cc_cfg "$cfg" api_base_url)"
  # The Claude Code SDK requests claude-* ids; the proxy maps them to the real
  # upstream OpenAI model, which is pr_model (the model actually served upstream).
  model="$(_cc_cfg "$cfg" pr_model)"
  key="$(_cc_cfg "$cfg" api_key)"

  if [ -z "$base" ] || [ -z "$model" ] || [ -z "$key" ]; then
    echo "ERROR: openai_proxy mode but llm_api.{api_base_url,pr_model,api_key} incomplete." >&2
    return 1
  fi
  case "${base}${model}${key}" in
    *'<'*'>'*)
      echo "ERROR: llm_api still has placeholder values (<...>). Fill them before an openai_proxy run." >&2
      return 1 ;;
  esac

  # Generate a filled proxy config from the committed template so the canonical
  # anthropic-tool fix (use_chat_completions_url_for_anthropic_messages,
  # enable_thinking:false, ...) stays in sync. Output is under artifacts/logs/
  # which is gitignored.
  local template="$block_dir/scripts/litellm_cc_proxy.example.yaml"
  local gen="$block_dir/artifacts/logs/.cc-litellm-proxy.generated.yaml"
  if [ ! -f "$template" ]; then
    echo "ERROR: missing proxy template $template" >&2
    return 1
  fi
  mkdir -p "$block_dir/artifacts/logs"
  sed -e "s|<UPSTREAM_OPENAI_BASE_URL>|${base}|g" \
      -e "s|<UPSTREAM_MODEL>|${model}|g" \
      -e "s|<API_KEY>|${key}|g" \
      "$template" > "$gen"

  # Resolve a PROXY-CAPABLE litellm binary. Not every `litellm` on PATH ships
  # the litellm[proxy] extra (e.g. swegen-env's lacks `backoff`), and such a
  # binary dies on startup — so probe each candidate's interpreter for the
  # proxy server import and skip the ones that can't serve. tracer's
  # litellm-venv is the canonical proxy-capable install. An explicit
  # LITELLM_BIN is trusted as-is.
  local bin="${LITELLM_BIN:-}"
  if [ -z "$bin" ]; then
    local cand
    for cand in \
      "$block_dir/../tracer/artifacts/env/litellm-venv/bin/litellm" \
      "$block_dir/artifacts/envs/swegen-env/bin/litellm" \
      "$(command -v litellm 2>/dev/null || true)"; do
      [ -n "$cand" ] && [ -x "$cand" ] || continue
      if _cc_litellm_proxy_ok "$cand"; then bin="$cand"; break; fi
    done
  fi
  if [ -z "$bin" ] || [ ! -x "$bin" ]; then
    echo "ERROR: no proxy-capable litellm found (litellm[proxy] missing). Point" \
         "LITELLM_BIN at tracer's litellm-venv or run: pip install 'litellm[proxy]'." >&2
    return 1
  fi

  # Free the port if a stale proxy holds it.
  if command -v lsof >/dev/null 2>&1 && lsof -ti:"$port" >/dev/null 2>&1; then
    echo "INFO: port $port busy — killing stale holder."
    lsof -ti:"$port" | xargs -r kill 2>/dev/null || true
    sleep 1
  fi

  local log="$block_dir/artifacts/logs/.cc-litellm-proxy.log"
  echo "INFO: starting LiteLLM CC proxy on :$port (bin=$bin, upstream=$model @ $base, log=$log)"
  env -i HOME="$HOME" PATH="$PATH" LANG="${LANG:-C.UTF-8}" HOST=0.0.0.0 \
    "$bin" --config "$gen" --port "$port" --host 127.0.0.1 \
    >"$log" 2>&1 &
  CC_PROXY_PID=$!

  # Wait up to 90s for /health, bailing early if the proxy process dies.
  local waited=0
  until curl -sf "http://127.0.0.1:${port}/health" >/dev/null 2>&1; do
    if ! kill -0 "$CC_PROXY_PID" 2>/dev/null; then
      echo "ERROR: LiteLLM CC proxy exited during startup. Tail of $log:" >&2
      tail -30 "$log" >&2 || true
      CC_PROXY_PID=""
      return 1
    fi
    sleep 2; waited=$((waited + 2))
    if [ "$waited" -ge 90 ]; then
      echo "ERROR: LiteLLM CC proxy did not become healthy within 90s. Tail of $log:" >&2
      tail -30 "$log" >&2 || true
      return 1
    fi
  done
  echo "INFO: LiteLLM CC proxy ready on :$port (PID=$CC_PROXY_PID)."
  return 0
}

cc_proxy_stop() {
  if [ -n "${CC_PROXY_PID:-}" ] && kill -0 "$CC_PROXY_PID" 2>/dev/null; then
    echo "INFO: stopping LiteLLM CC proxy (PID=$CC_PROXY_PID)."
    kill "$CC_PROXY_PID" 2>/dev/null || true
    wait "$CC_PROXY_PID" 2>/dev/null || true
  fi
  CC_PROXY_PID=""
}
