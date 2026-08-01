#!/usr/bin/env bash
# Verify curator's configured LLM endpoints actually return text — both the
# OpenAI path (pr_model) and, in native mode, the Anthropic path (task_model)
# that writes verifiable_tasks.txt.
# Exit: 0 PASS, 1 FAIL, 77 SKIP (unfilled config or transient gateway).
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$BLOCK_DIR/../.." && pwd)"
CONFIG="${CURATOR_CONFIG:-$BLOCK_DIR/config.yaml}"

cfg() { python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
node = (d.get("runtime_info") or {}).get("input", {}).get("llm_api", {}) or {}
print(str(node.get(sys.argv[2]) or "").strip())
PY
}

ANTHROPIC_ARGS=()
if [[ "$(cfg cc_provider_mode)" == "native" ]]; then
  # openai_proxy points anthropic_base_url at a local proxy that only exists
  # during a run, so probing it here would fail for the wrong reason.
  ANTHROPIC_ARGS=(--anthropic-base-url "$(cfg anthropic_base_url)"
                  --anthropic-model "$(cfg task_model)")
fi

exec python3 "$ROOT_DIR/scripts/probe_llm_endpoint.py" \
  --label "curator LLM endpoint" \
  --base-url "$(cfg api_base_url)" \
  --model "$(cfg pr_model)" \
  --api-key "$(cfg api_key)" \
  "${ANTHROPIC_ARGS[@]}" \
  "$@"
