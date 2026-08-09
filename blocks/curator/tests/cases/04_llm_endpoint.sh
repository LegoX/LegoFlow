#!/usr/bin/env bash
# CI test 04: LLM endpoint completes a real chat.completions request.
# Mirrors /curator:check — uses legoflow_curator.llm_env.hydrate_cross_provider_env() so a
# misconfigured cross-provider env is caught here, not on first task.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$BLOCK_DIR/scripts/load_runtime_env.sh"
load_runtime_env >/dev/null 2>&1 || true

VENV_PY="$BLOCK_DIR/artifacts/envs/legoflow-curator-env/bin/python"
[[ -x "$VENV_PY" ]] || { echo "FAIL: legoflow-curator venv python missing — run /curator:setup"; exit 1; }

PYTHONPATH="$BLOCK_DIR/repos/legoflow-curator/src:${PYTHONPATH:-}" "$VENV_PY" - <<'PY' || exit $?
import sys, time
try:
    from openai import OpenAI, APITimeoutError, APIConnectionError, InternalServerError
    from legoflow_curator.llm_env import hydrate_cross_provider_env, get_openai_compatible_config
except Exception as e:
    print(f"FAIL: import error: {e}", file=sys.stderr)
    sys.exit(1)

hydrate_cross_provider_env()
try:
    model, key, base = get_openai_compatible_config()
except Exception as e:
    print(f"FAIL: get_openai_compatible_config: {e}", file=sys.stderr)
    sys.exit(1)

if not (model and key and base):
    print(f"FAIL: incomplete LLM config (model={bool(model)}, key={bool(key)}, base={bool(base)})", file=sys.stderr)
    sys.exit(1)

client = OpenAI(api_key=key, base_url=base, timeout=30)

# Retry transient upstream issues (timeout / 5xx / conn refused). Real config
# errors (401/403/400) raise immediately and are NOT retried.
TRANSIENT = (APITimeoutError, APIConnectionError, InternalServerError)
ATTEMPTS = 3
last_exc = None
for i in range(1, ATTEMPTS + 1):
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=16,
        )
        if not resp.choices:
            print("FAIL: response has no choices", file=sys.stderr)
            sys.exit(1)
        print(f"PASS: LLM endpoint OK ({base}, model={model}, attempt={i})")
        sys.exit(0)
    except TRANSIENT as e:
        last_exc = e
        print(f"WARN: transient {type(e).__name__} on attempt {i}/{ATTEMPTS}: {e}", file=sys.stderr)
        if i < ATTEMPTS:
            time.sleep(5 * i)
        continue
    except Exception as e:
        # Auth/4xx/protocol errors — config-side, NOT transient.
        print(f"FAIL: chat.completions.create({model}) raised: {e}", file=sys.stderr)
        sys.exit(1)

# Exhausted retries on a transient class. Skip rather than redden CI for an
# upstream problem we can't fix from here.
print(f"SKIP: upstream LLM endpoint unreachable after {ATTEMPTS} transient {type(last_exc).__name__} failures ({last_exc})", file=sys.stderr)
sys.exit(77)
PY
