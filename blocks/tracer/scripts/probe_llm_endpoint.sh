#!/usr/bin/env bash
# Verify tracer's configured LLM endpoint actually returns text.
# Exit: 0 PASS, 1 FAIL, 77 SKIP (unfilled config or transient gateway).
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$BLOCK_DIR/../.." && pwd)"
CONFIG="${TRAJGEN_CONFIG:-${TRACER_CONFIG:-$BLOCK_DIR/config.yaml}}"

cfg() { python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
node = (d.get("runtime_info") or {}).get("input", {}).get("llm_api", {}) or {}
print(str(node.get(sys.argv[2]) or "").strip())
PY
}

BASE_URL="${OPENAI_API_BASE_URL:-${OPENAI_BASE_URL:-$(cfg api_base_url)}}"
MODEL_RAW="${OPENAI_MODEL:-${ANTHROPIC_MODEL:-$(cfg model)}}"
API_KEY="${OPENAI_API_KEY:-${ANTHROPIC_API_KEY:-${ANTHROPIC_AUTH_TOKEN:-$(cfg api_key)}}}"

# llm_api.model is the LiteLLM spec (provider/model) the per-job proxy consumes;
# this probe talks to the RAW upstream, which only knows the bare served name and
# 404s on the prefixed form — so strip the prefix before probing.
exec python3 "$ROOT_DIR/scripts/probe_llm_endpoint.py" \
  --label "tracer LLM endpoint" \
  --base-url "$BASE_URL" \
  --model "${MODEL_RAW##*/}" \
  --api-key "$API_KEY" \
  "$@"
