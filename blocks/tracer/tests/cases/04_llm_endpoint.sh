#!/usr/bin/env bash
# CI test 04: the configured LLM endpoint returns actual text.
#
# This used to be a GET /models catalog check. A catalog listing proves nothing:
# gateways answer it from local config, so /models can return 200 while every
# completion 502s or comes back with empty content. The probe now requires a
# non-empty reply and, on failure, reports whether a neighbouring URL/model
# shape (missing /v1, stray provider prefix) is the actual problem.
#
# Exit: 0 PASS, 1 FAIL, 77 SKIP (unfilled config or transient gateway).
set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

read_key() { python3 - "$BLOCK_DIR/config.yaml" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
llm = ((d.get("runtime_info") or {}).get("input") or {}).get("llm_api") or {}
print((llm.get(sys.argv[2]) or "").strip())
PY
}
if [[ -z "$(read_key api_base_url)" || -z "$(read_key api_key)" ]]; then
  echo "SKIP: llm_api endpoint credentials are not injected in this cases-only run"
  exit 77
fi

bash "$BLOCK_DIR/scripts/probe_llm_endpoint.sh" --attempts 3
