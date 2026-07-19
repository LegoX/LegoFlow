#!/usr/bin/env bash
# CI smoke verifier for tracer.
#
# Pass gate (pipeline health, NOT solving SWE-bench): at least one trial under
# artifacts/jobs/smoke/<job>/<task>/result.json has the verifier running to a
# terminal reward — agent ran end-to-end, container built, harbor scored. The
# reward itself may be 0; the smoke is about the pipeline, not the model.
# This mirrors evaluator's "clean scored trial" semantics.
#
# Resolved (reward>0) is reported but does not gate: at tracer's smoke scale
# (10 tasks, 2 concurrent, 30-40 min budget) a capable model legitimately solves
# 0–2 — gating on RESOLVED makes the smoke flaky purely on LLM luck.
#
# Exit 0 = PASS, 77 = SKIP (no result.json produced — earlier SKIP), 1 = FAIL.

set -uo pipefail

BLOCK_DIR="${BLOCK_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SMOKE_JOBS_DIR="$BLOCK_DIR/artifacts/jobs/smoke"

if [[ ! -d "$SMOKE_JOBS_DIR" ]]; then
  echo "SKIP: no $SMOKE_JOBS_DIR — start.sh likely failed before harbor ran"
  exit 77
fi

SCAN="$(SMOKE_JOBS_DIR="$SMOKE_JOBS_DIR" python3 - <<'PY'
import json, os, glob
root = os.environ["SMOKE_JOBS_DIR"]
total, scored, resolved, parse_err = 0, 0, 0, 0
for path in glob.glob(os.path.join(root, "*", "*", "result.json")):
    total += 1
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        parse_err += 1; continue
    vr = data.get("verifier_result")
    if not isinstance(vr, dict):
        continue
    rewards = vr.get("rewards")
    if not isinstance(rewards, dict) or not rewards:
        continue
    scored += 1
    if any(isinstance(v, (int, float)) and v > 0 for v in rewards.values()):
        resolved += 1
print(f"SCORED:{scored} RESOLVED:{resolved} TOTAL:{total} PARSE_ERR:{parse_err}")
PY
)"
echo "INFO: trial result.json scan: $SCAN"

total=$(sed -n 's/.*TOTAL:\([0-9]*\).*/\1/p' <<<"$SCAN")
scored=$(sed -n 's/.*SCORED:\([0-9]*\).*/\1/p' <<<"$SCAN")
total=${total:-0}; scored=${scored:-0}

if [[ "$total" == "0" ]]; then
  echo "SKIP: no result.json files written under $SMOKE_JOBS_DIR"
  exit 77
fi
if [[ "$scored" -ge 1 ]]; then
  echo "PASS: tracer pipeline produced >=1 scored trial ($SCAN)"
  exit 0
fi
echo "FAIL: 0 scored trials — every trial errored before the verifier could run ($SCAN)"
exit 1
