#!/usr/bin/env bash
# CI test 04: evaluator upstream LLM endpoint — best-effort reachability + model check.
#
# A proxy-mediated gateway can be identified with EVAL_GATEWAY_HOST_SUFFIX;
# its edge/gating errors downgrade to SKIP. The infrastructure-neutral checked-in
# config also SKIPs an unavailable loopback endpoint unless
# EVAL_REQUIRE_LLM_ENDPOINT=1.
# The one deterministic failure we DO catch: a clean 200 whose /models catalog
# does not contain the configured model (the endpoint serves something else).

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

read_key() { python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
cur = (d.get("runtime_info") or {}).get("input", {}).get("llm_api", {})
print((cur.get(sys.argv[2]) or "").strip())
PY
}
MODEL_API_BASE_URL="$(read_key api_base_url)"
MODEL_API_KEY="$(read_key api_key)"
MODEL_API_MODEL="$(read_key model)"
MODEL_API_HOST="$(MODEL_API_BASE_URL="$MODEL_API_BASE_URL" python3 - <<'PY'
import os
from urllib.parse import urlparse
print((urlparse(os.environ["MODEL_API_BASE_URL"]).hostname or "").lower())
PY
)"

if [[ -z "$MODEL_API_BASE_URL" || -z "$MODEL_API_KEY" ]]; then
  echo "SKIP: llm_api endpoint credentials are not injected in this cases-only run"
  exit 77
fi
[[ -n "$MODEL_API_MODEL" ]] || { echo "FAIL: model not configured"; exit 1; }

# `human` is the fill-marker for "must be filled in before a run" (see
# BLOCK_DEFINITION.md's fill-marker convention) — not a real URL. Probing it
# builds `human/models` and dies on `ValueError: unknown url type`, so treat it
# as unfilled config and SKIP, matching tracer's equivalent case.
if [[ "$MODEL_API_BASE_URL" == "human" || "$MODEL_API_KEY" == "human" || "$MODEL_API_MODEL" == "human" ]]; then
  echo "SKIP: llm_api still has an unfilled \`human\` placeholder — fill runtime_info.input.llm_api before this check can probe it"
  exit 77
fi

RESULT="$(MODEL_API_BASE_URL="$MODEL_API_BASE_URL" MODEL_API_KEY="$MODEL_API_KEY" MODEL_API_MODEL="$MODEL_API_MODEL" python3 - <<'PY'
import json, os, sys, urllib.request, urllib.error
base = os.environ["MODEL_API_BASE_URL"].rstrip("/")
key  = os.environ.get("MODEL_API_KEY", "")
want = os.environ["MODEL_API_MODEL"].split("/", 1)[-1]
req = urllib.request.Request(
    f"{base}/models",
    headers={"Authorization": f"Bearer {key}", "User-Agent": "curl/8.5.0"},
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read().decode("utf-8", "replace"))
except urllib.error.HTTPError as e:
    print(f"HTTP:{e.code}"); sys.exit(0)
except Exception as e:
    print(f"NET:{type(e).__name__}:{e}"); sys.exit(0)
ids = [m.get("id") for m in (body.get("data") or [])]
# vLLM without --served-model-name publishes the checkpoint PATH as the id
# (e.g. /data/models/Qwen3.6-35B-A3B) yet accepts the basename in requests —
# match on either the full id or its basename.
names = {i for i in ids if i} | {i.rsplit("/", 1)[-1] for i in ids if i}
print(f"OK:{len(ids)}:{int(want in names)}")
PY
)"

# Gateway/proxy-mediated codes that are not necessarily evaluator misconfigurations.
is_gateway_code() { case "$1" in 401|403|429|502|503|521|522|523|525|530) return 0 ;; *) return 1 ;; esac; }
is_gateway_host() {
  local suffix="${EVAL_GATEWAY_HOST_SUFFIX:-}"
  suffix="${suffix#.}"
  [[ -n "$suffix" ]] && [[ "$MODEL_API_HOST" == "$suffix" || "$MODEL_API_HOST" == *."$suffix" ]]
}
is_loopback_host() { [[ "$MODEL_API_HOST" == "127.0.0.1" || "$MODEL_API_HOST" == "localhost" || "$MODEL_API_HOST" == "::1" ]]; }

case "$RESULT" in
  OK:*)
    IFS=':' read -r _ n_models has_model <<<"$RESULT"
    if [[ "$has_model" == "1" ]]; then
      echo "PASS: LLM endpoint reachable ($n_models models), $MODEL_API_MODEL present"
    else
      echo "FAIL: configured model ${MODEL_API_MODEL##*/} not in upstream /models catalog ($n_models entries)"
      exit 1
    fi
    ;;
  HTTP:*)
    code="${RESULT#HTTP:}"
    if is_gateway_host && is_gateway_code "$code"; then
      echo "SKIP: configured gateway $MODEL_API_HOST returned $code from this shell"
      exit 77
    fi
    echo "FAIL: LLM endpoint returned HTTP $code"; exit 1 ;;
  NET:*)
    if is_gateway_host; then
      echo "SKIP: configured gateway is not directly reachable from this host (${RESULT#NET:})"
      exit 77
    fi
    if is_loopback_host && [[ "${EVAL_REQUIRE_LLM_ENDPOINT:-0}" != "1" ]]; then
      echo "SKIP: example loopback endpoint is not running (${RESULT#NET:})"
      exit 77
    fi
    echo "FAIL: local model endpoint is not reachable (${RESULT#NET:})"; exit 1 ;;
esac
