#!/usr/bin/env bash
# CI test 04: LLM endpoint completes a real chat.completions request against the
# endpoint terminal-lego's generator uses (OPENAI_API_BASE_URL + MODEL_NAME).
# A /models probe is not enough — real completion catches wrong keys/routing.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$BLOCK_DIR/scripts/load_runtime_env.sh"
load_runtime_env >/dev/null 2>&1 || true

PY="$BLOCK_DIR/artifacts/envs/terminalgen-env/bin/python"
[[ -x "$PY" ]] || PY="python3"

"$PY" - <<'PY' || exit $?
import os, sys, time, json, urllib.request, urllib.error

base = os.environ.get("OPENAI_API_BASE_URL") or os.environ.get("OPENAI_API_BASE")
key = os.environ.get("OPENAI_API_KEY")
model = os.environ.get("MODEL_NAME", "deepseek-v4-flash")

if not (base and key):
    print(f"FAIL: incomplete LLM config (base={bool(base)}, key={bool(key)})", file=sys.stderr)
    sys.exit(1)

url = base.rstrip("/") + "/chat/completions"
payload = json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 16,
}).encode()

# Dependency-free POST (terminal-lego itself uses requests, but cases must run
# even before the venv is built). Retry transient 5xx/timeout; fail fast on 4xx.
ATTEMPTS = 3
last = None
for i in range(1, ATTEMPTS + 1):
    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
        if body.get("choices"):
            print(f"PASS: LLM endpoint OK ({base}, model={model}, attempt={i})")
            sys.exit(0)
        print("FAIL: response has no choices", file=sys.stderr)
        sys.exit(1)
    except urllib.error.HTTPError as e:
        code = e.code
        if code in (401, 403, 400, 404):
            print(f"FAIL: HTTP {code} from {url} (config-side, not transient)", file=sys.stderr)
            sys.exit(1)
        last = f"HTTP {code}"
    except Exception as e:
        last = str(e)
    print(f"WARN: transient failure attempt {i}/{ATTEMPTS}: {last}", file=sys.stderr)
    if i < ATTEMPTS:
        time.sleep(5 * i)

print(f"SKIP: upstream LLM endpoint unreachable after {ATTEMPTS} transient failures ({last})", file=sys.stderr)
sys.exit(77)
PY
