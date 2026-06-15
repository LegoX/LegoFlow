#!/usr/bin/env bash
# CI test 04: LLM endpoint completes a real chat.completions request.
# Mirrors /swegen:check — uses swegen.llm_env.hydrate_cross_provider_env() so a
# misconfigured cross-provider env is caught here, not on first task.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$BLOCK_DIR/scripts/load_runtime_env.sh"
load_runtime_env >/dev/null 2>&1 || true

VENV_PY="$BLOCK_DIR/artifacts/envs/swegen-env/bin/python"
[[ -x "$VENV_PY" ]] || { echo "FAIL: swegen venv python missing — run /swegen:setup"; exit 1; }

PYTHONPATH="$BLOCK_DIR/repos/swegen/src:${PYTHONPATH:-}" "$VENV_PY" - <<'PY' || exit 1
import os, sys
try:
    from openai import OpenAI
    from swegen.llm_env import hydrate_cross_provider_env, get_openai_compatible_config
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
try:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=16,
    )
except Exception as e:
    print(f"FAIL: chat.completions.create({model}) raised: {e}", file=sys.stderr)
    sys.exit(1)

if not resp.choices:
    print("FAIL: response has no choices", file=sys.stderr)
    sys.exit(1)

print(f"PASS: LLM endpoint OK ({base}, model={model})")
PY
