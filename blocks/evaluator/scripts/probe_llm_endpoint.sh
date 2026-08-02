#!/usr/bin/env bash
# Verify evaluator's configured LLM endpoint actually returns text.
# probe_llm_completion.sh remains the launch gate with its origin-health
# classification; this adds the URL/model shape diagnosis on top.
# Exit: 0 PASS, 1 FAIL, 77 SKIP (unfilled config or transient gateway).
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$BLOCK_DIR/../.." && pwd)"
CONFIG="${EVAL_CONFIG:-$BLOCK_DIR/config.yaml}"

cfg() { python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
node = (d.get("runtime_info") or {}).get("input", {}).get("llm_api", {}) or {}
print(str(node.get(sys.argv[2]) or "").strip())
PY
}

# llm_api.model is the LiteLLM spec (provider/model) the per-job proxy consumes;
# this probe talks to the RAW upstream, which only knows the bare served name and
# 404s on the prefixed form — so strip the prefix before probing.
exec python3 "$ROOT_DIR/scripts/probe_llm_endpoint.py" \
  --label "evaluator LLM endpoint" \
  --base-url "$(cfg api_base_url)" \
  --model "$(cfg model | sed 's|^[^/]*/||')" \
  --api-key "$(cfg api_key)" \
  "$@"
