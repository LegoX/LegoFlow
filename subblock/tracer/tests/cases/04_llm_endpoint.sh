#!/usr/bin/env bash
# CI test 04: tracer LLM endpoint reachable; configured model in /models catalog.
# (Light probe — /tracer:check intentionally avoids chat.completions because
# the tracer proxy stack is what consumes tokens, not preflight.)
#
# The request uses the configured API key. Cloudflare 5xx/52x responses are
# retried briefly because edge/origin transitions can be transient, but a
# persistent Cloudflare edge failure is SKIPped so unrelated changes are not
# blocked by shared gateway health. Non-Cloudflare and auth failures still FAIL.

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
import json, os, sys, time, urllib.request, urllib.error
base = os.environ["MODEL_API_BASE_URL"].rstrip("/")
key  = os.environ.get("MODEL_API_KEY", "")
want = os.environ["MODEL_API_MODEL"].split("/", 1)[-1]
transient_cf = {502, 503, 521, 522, 523, 525, 530}
for attempt in range(3):
    req = urllib.request.Request(
        f"{base}/models",
        headers={"Authorization": f"Bearer {key}", "User-Agent": "curl/8.5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        break
    except urllib.error.HTTPError as e:
        server = (e.headers.get("server") or "").lower() if e.headers else ""
        if e.code in transient_cf and "cloudflare" in server and attempt < 2:
            time.sleep(attempt + 1)
            continue
        print(f"HTTP:{e.code}:{server}"); sys.exit(0)
    except Exception as e:
        print(f"NET:{type(e).__name__}:{e}"); sys.exit(0)
ids = [m.get("id") for m in (body.get("data") or [])]
# vLLM without --served-model-name publishes the checkpoint PATH as the id
# (e.g. /data/models/Qwen3.6-35B-A3B) yet accepts the basename in requests —
# match on either the full id or its basename (aligns with eval's 04 case).
names = {i for i in ids if i} | {i.rsplit("/", 1)[-1] for i in ids if i}
print(f"OK:{len(ids)}:{int(want in names)}")
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
    response="${RESULT#HTTP:}"
    code="${response%%:*}"
    server="${response#*:}"
    case "$code" in
      502|503|521|522|523|525|530)
        if [[ "$server" == *cloudflare* ]]; then
          echo "SKIP: Cloudflare gateway still returned $code after retries"
          exit 77
        fi
        ;;
    esac
    echo "FAIL: LLM endpoint returned $code"; exit 1 ;;
  NET:*)
    echo "FAIL: LLM endpoint unreachable: ${RESULT#NET:}"; exit 1 ;;
esac
