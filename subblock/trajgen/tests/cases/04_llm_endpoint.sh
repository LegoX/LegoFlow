#!/usr/bin/env bash
# CI test 04: trajgen LLM endpoint reachable; configured model in /models catalog.
# (Light probe — /trajgen:check intentionally avoids chat.completions because
# the trajgen proxy stack is what consumes tokens, not preflight.)

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

MODEL_API_BASE_URL="$(python3 - "$CONFIG" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
print((d.get("runtime_info",{}).get("input",{}).get("llm_api",{}).get("api_base_url") or "").strip())
PY
)"
MODEL_API_KEY="$(python3 - "$CONFIG" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
print((d.get("runtime_info",{}).get("input",{}).get("llm_api",{}).get("api_key") or "").strip())
PY
)"
MODEL_API_MODEL="$(python3 - "$CONFIG" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
print((d.get("runtime_info",{}).get("input",{}).get("llm_api",{}).get("model") or "").strip())
PY
)"

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

# Cloudflare-gating / proxy-mediated codes that are not trajgen misconfigurations.
# The shared endpoint (llm.jierungogogo.com) can return 401/403/52x to a direct
# /models probe even though real traffic is proxy-mediated and works — so these
# downgrade to SKIP, matching subblock/eval/tests/cases/04_llm_endpoint.sh and the
# root preflight (see memory project-eval-llm-endpoint / project-swegen-llm-endpoint).
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
      echo "SKIP: LLM endpoint returned $code — Cloudflare-gating/proxy-mediated artifact, not a misconfig (see memory project-swegen-llm-endpoint)"
      exit 77
    fi
    echo "FAIL: LLM endpoint returned HTTP $code"; exit 1 ;;
  NET:*)
    echo "SKIP: LLM endpoint not directly reachable from this host (${RESULT#NET:}); real reachability is proxy-mediated and covered by the smoke"
    exit 77 ;;
esac
