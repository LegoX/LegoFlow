#!/usr/bin/env bash
# Verify tracer's configured LLM endpoint actually returns text.
# Exit: 0 PASS, 1 FAIL, 77 SKIP (unfilled config or transient gateway).
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$BLOCK_DIR/../.." && pwd)"
CONFIG="${TRACER_CONFIG:-$BLOCK_DIR/config.yaml}"

cfg() { python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
node = (d.get("runtime_info") or {}).get("input", {}).get("llm_api", {}) or {}
print(str(node.get(sys.argv[2]) or "").strip())
PY
}

exec python3 "$ROOT_DIR/scripts/probe_llm_endpoint.py" \
  --label "tracer LLM endpoint" \
  --base-url "$(cfg api_base_url)" \
  --model "$(cfg model)" \
  --api-key "$(cfg api_key)" \
  "$@"
