#!/usr/bin/env bash
# Live launch-gate probe for /evaluator:check: send a minimal real chat completion
# to the configured upstream and classify the result by ORIGIN HEALTH.
#
# Why a completion and not GET /models: LiteLLM (and many gateways) answer
# /models from local config without contacting the model backend, so /models
# can return 200 while every completion 502s. A 100/100-error run on
# 2026-06-14 was caused by trusting a /models 200 over a dead origin.
#
# Usage:
#   bash scripts/probe_llm_completion.sh                 # read config.yaml
#   bash scripts/probe_llm_completion.sh <base_url> <model> [api_key]
#
# Exit codes: 0 = PASS (origin served a valid completion; safe to launch)
#             1 = FAIL (5xx/network, malformed 2xx, or local auth failure)
#            77 = WARN (configured gateway 401/403 or app-level 400/404/422)
# Set EVAL_GATEWAY_HOST_SUFFIX for gateways whose edge can mask origin auth.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${EVAL_CONFIG:-$BLOCK_DIR/config.yaml}"

cfg() { python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
cur = (d.get("runtime_info") or {}).get("input", {}).get("llm_api", {})
print((cur.get(sys.argv[2]) or "").strip())
PY
}

BASE_URL="${1:-$(cfg api_base_url)}"
MODEL_RAW="${2:-$(cfg model)}"
API_KEY="${3:-$(cfg api_key)}"

[[ -n "$BASE_URL" && -n "$MODEL_RAW" ]] || { echo "FAIL: api_base_url or model not configured"; exit 1; }
# Strip a leading provider prefix (openai/, hosted_vllm/, …); the raw upstream
# wants the bare served name. Mirrors the /models basename convention.
MODEL="${MODEL_RAW#*/}"

RESULT="$(BASE_URL="$BASE_URL" MODEL="$MODEL" API_KEY="$API_KEY" python3 - <<'PY'
import json, os, urllib.request, urllib.error
base = os.environ["BASE_URL"].rstrip("/")
payload = json.dumps({
    "model": os.environ["MODEL"],
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 4,
}).encode()
req = urllib.request.Request(
    f"{base}/chat/completions",
    data=payload,
    method="POST",
    headers={
        "Authorization": f"Bearer {os.environ.get('API_KEY','')}",
        "Content-Type": "application/json",
        "User-Agent": "curl/8.5.0",
    },
)
try:
    with urllib.request.urlopen(req, timeout=25) as r:
        server = (r.headers.get("server") or "").lower()
        body = r.read()
        try:
            response = json.loads(body)
        except Exception:
            print(f"BAD2XX:{r.status}:invalid_json:{server}")
        else:
            choices = response.get("choices") if isinstance(response, dict) else None
            if isinstance(choices, list) and choices:
                print(f"HTTP:{r.status}:{server}")
            else:
                print(f"BAD2XX:{r.status}:missing_choices:{server}")
except urllib.error.HTTPError as e:
    server = (e.headers.get("server") or "").lower() if e.headers else ""
    print(f"HTTP:{e.code}:{server}")
except Exception as e:
    print(f"NET:{type(e).__name__}:{e}")
PY
)"

is_configured_gateway() {
  local host="$1"
  local suffix="${EVAL_GATEWAY_HOST_SUFFIX:-}"
  suffix="${suffix#.}"
  [[ -n "$suffix" ]] && [[ "$host" == "$suffix" || "$host" == *."$suffix" ]]
}

case "$RESULT" in
  HTTP:200:*)
    echo "PASS: upstream served a completion (HTTP 200) — origin healthy [$BASE_URL model=$MODEL]"
    exit 0 ;;
  HTTP:5*|HTTP:502:*|HTTP:503:*|HTTP:504:*|HTTP:52[0-6]:*)
    code="$(cut -d: -f2 <<<"$RESULT")"; server="$(cut -d: -f3- <<<"$RESULT")"
    edge=""
    [[ "$server" == *cloudflare* ]] && edge=" (Cloudflare edge 5xx — CF has no healthy origin)"
    echo "FAIL: upstream returned HTTP $code$edge — model backend is DOWN; do NOT launch [$BASE_URL]"
    echo "      CF marks 502 retryable (retry_after 60s). Re-probe after ~60s; only a CLEARING 502 is non-fatal."
    exit 1 ;;
  NET:*)
    echo "FAIL: upstream unreachable (${RESULT#NET:}) — connection/timeout; do NOT launch [$BASE_URL]"
    exit 1 ;;
  BAD2XX:*)
    echo "FAIL: upstream returned a 2xx response without a valid chat-completion choices array (${RESULT#BAD2XX:}) — do NOT launch [$BASE_URL]"
    exit 1 ;;
  HTTP:401:*|HTTP:403:*)
    code="$(cut -d: -f2 <<<"$RESULT")"
    host="$(BASE_URL="$BASE_URL" python3 - <<'PY'
import os
from urllib.parse import urlparse
print((urlparse(os.environ["BASE_URL"]).hostname or "").lower())
PY
)"
    if is_configured_gateway "$host"; then
      echo "WARN: upstream returned HTTP $code — auth/gateway ambiguity on $host."
      echo "      Re-probe from a non-sandboxed shell on the node before trusting; this is NOT origin-down (502)."
      exit 77
    fi
    echo "FAIL: upstream returned HTTP $code from $host — credentials/API key are invalid; do NOT launch."
    exit 1 ;;
  HTTP:400:*|HTTP:404:*|HTTP:422:*)
    code="$(cut -d: -f2 <<<"$RESULT")"
    echo "WARN: upstream app responded HTTP $code (origin is UP) but rejected the probe — likely the model name."
    echo "      Verify llm_api.model='$MODEL_RAW' (upstream wants the bare name '$MODEL')."
    exit 77 ;;
  HTTP:*)
    code="$(cut -d: -f2 <<<"$RESULT")"
    echo "WARN: upstream returned HTTP $code — unexpected; inspect manually [$BASE_URL]"
    exit 77 ;;
  *)
    echo "FAIL: probe inconclusive: $RESULT"; exit 1 ;;
esac
