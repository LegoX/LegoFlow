#!/usr/bin/env bash
# CI test 04: tracer LLM endpoint reachable; configured model in /models catalog.
# (Light probe — /tracer:check intentionally avoids chat.completions because
# the tracer proxy stack is what consumes tokens, not preflight.)

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
print(f"OK:{len(ids)}:{int(want in ids)}")
PY
)"

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
    echo "FAIL: LLM endpoint returned ${RESULT#HTTP:}"; exit 1 ;;
  NET:*)
    echo "FAIL: LLM endpoint unreachable: ${RESULT#NET:}"; exit 1 ;;
esac
