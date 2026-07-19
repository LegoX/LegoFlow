#!/usr/bin/env bash
# CI test 10: protect the generated Harbor command and launch-gate ordering
# without creating a job or spending model tokens.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$BLOCK_DIR/scripts/start.sh" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

required = {
    "isolated config override": 'CONFIG="${EVAL_CONFIG:-$BLOCK_DIR/config.yaml}"',
    "versioned dataset construction": 'HARBOR_DATASET_SPEC="${DATASET_NAME}@${DATASET_VERSION}"',
    "versioned dataset command": '--dataset $(printf \'%q\' "$HARBOR_DATASET_SPEC")',
    "static dryrun": 'bash "$BLOCK_DIR/scripts/dryrun.sh"',
    "completion launch gate": 'bash "$BLOCK_DIR/scripts/probe_llm_completion.sh"',
    "proxy launch": 'echo "=== starting LiteLLM proxy ==="',
    "failed-run analysis does not prepare datasets": 'export JOB_ANALYSIS_PREPARE_DATASET=0',
}
missing = [label for label, snippet in required.items() if snippet not in text]
if missing:
    raise SystemExit("FAIL: start.sh contract missing: " + ", ".join(missing))

if not (
    text.index(required["static dryrun"])
    < text.index(required["completion launch gate"])
    < text.index(required["proxy launch"])
):
    raise SystemExit("FAIL: start.sh must run dryrun then completion probe before proxy launch")

print("PASS: start.sh preserves dataset@version and launch-gate ordering")
PY

grep -Fq 'MODEL_PATH="$BLOCK_DIR/$MODEL_PATH"' \
  "$BLOCK_DIR/scripts/serve_local_model.sh" || {
  echo "FAIL: serve_local_model.sh does not resolve configured relative paths from BLOCK_DIR"
  exit 1
}
