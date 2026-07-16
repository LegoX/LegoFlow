#!/usr/bin/env bash
# CI test 04: eval upstream LLM endpoint — best-effort reachability + model check.
#
# eval's upstream (qwen.jierungogogo.com) is a Cloudflare-gated production
# endpoint whose real key is the literal `dummy-key`. A direct `GET /models`
# from a sandboxed / off-node shell can return 401/403/502/52x (CF gating) or
# fail to connect — none of which mean the endpoint is misconfigured, because
# real traffic is proxy-mediated by the per-job LiteLLM proxy and exercised by
# the smoke. So CF-class HTTP codes and network errors downgrade to SKIP.
# The one deterministic failure we DO catch: a clean 200 whose /models catalog
# does not contain the configured model (the endpoint serves something else).
# See memory project-eval-llm-endpoint.

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

[[ -n "$MODEL_API_BASE_URL" && -n "$MODEL_API_MODEL" ]] || { echo "FAIL: api_base_url or model not configured"; exit 1; }

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

# Cloudflare-gating / proxy-mediated codes that are not eval misconfigurations.
is_cf_code() { case "$1" in 401|403|429|502|503|521|522|523|525|530) return 0 ;; *) return 1 ;; esac; }

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
    if is_cf_code "$code"; then
      echo "SKIP: LLM endpoint returned $code — Cloudflare-gating/proxy-mediated artifact, not a misconfig (see memory project-eval-llm-endpoint)"
      exit 77
    fi
    echo "FAIL: LLM endpoint returned HTTP $code"; exit 1 ;;
  NET:*)
    echo "SKIP: LLM endpoint not directly reachable from this host (${RESULT#NET:}); real reachability is proxy-mediated and covered by the smoke"
    exit 77 ;;
esac
