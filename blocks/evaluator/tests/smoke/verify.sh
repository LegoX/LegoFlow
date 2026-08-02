#!/usr/bin/env bash
# CI smoke verifier for evaluator.
# Pass: at least one trial reached a "clean scored" state — verifier ran AND
# recorded a reward (0 or 1) AND that trial did NOT raise an agent exception.
# Exit 0 = PASS, 1 = FAIL. A requested smoke that produced no result is a
# launch failure, not a skip.

set -uo pipefail

BLOCK_DIR="${BLOCK_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SMOKE_JOBS_DIR="$BLOCK_DIR/artifacts/jobs/smoke"
RUN_START_FILE="$SMOKE_JOBS_DIR/.run-start"

if [[ ! -d "$SMOKE_JOBS_DIR" ]]; then
  echo "FAIL: no $SMOKE_JOBS_DIR — smoke launch produced no jobs directory"
  exit 1
fi
if [[ ! -s "$RUN_START_FILE" ]] || ! [[ "$(cat "$RUN_START_FILE")" =~ ^[0-9]+$ ]]; then
  echo "FAIL: no valid $RUN_START_FILE — cannot distinguish this run from stale results"
  exit 1
fi

SCAN="$(SMOKE_JOBS_DIR="$SMOKE_JOBS_DIR" SMOKE_RUN_STARTED_AT="$(cat "$RUN_START_FILE")" python3 - <<'PY'
import json, os, glob
root = os.environ["SMOKE_JOBS_DIR"]
started_at = int(os.environ["SMOKE_RUN_STARTED_AT"])
results = [
    path for path in glob.glob(os.path.join(root, "*", "result.json"))
    if os.path.getmtime(path) >= started_at
]
results.sort(key=os.path.getmtime)
if not results:
    print("CLEAN:0 EVAL:0 RESOLVED:0 ERRORED:0 TOTAL:0 (no current result.json)")
    raise SystemExit
try:
    with open(results[-1], encoding="utf-8") as fh:
        data = json.load(fh)
except Exception as e:
    print(f"CLEAN:0 EVAL:0 RESOLVED:0 ERRORED:0 TOTAL:0 (parse_error:{e})")
    raise SystemExit
stats = data.get("stats") or {}
evals = stats.get("evals") or {}
evaluated, resolved, errored = set(), set(), set()
for ds in evals.values():
    if not isinstance(ds, dict):
        continue
    for by_value in (ds.get("reward_stats") or {}).values():
        for value, names in (by_value or {}).items():
            try:
                positive = float(value) > 0
            except (TypeError, ValueError):
                positive = False
            for n in names or []:
                evaluated.add(n)
                if positive:
                    resolved.add(n)
    for names in (ds.get("exception_stats") or {}).values():
        for n in names or []:
            errored.add(n)
clean = evaluated - errored
total = data.get("n_total_trials") or stats.get("n_trials") or 0
print(f"CLEAN:{len(clean)} EVAL:{len(evaluated)} RESOLVED:{len(resolved)} ERRORED:{len(errored)} TOTAL:{total}")
PY
)"
echo "INFO: result.json scan: $SCAN"

if grep -q "no current result.json" <<<"$SCAN"; then
  echo "FAIL: no result.json newer than this smoke's start marker under $SMOKE_JOBS_DIR"
  exit 1
fi

clean="$(sed -n 's/.*CLEAN:\([0-9]*\).*/\1/p' <<<"$SCAN")"
clean="${clean:-0}"

if [[ "$clean" -ge 1 ]]; then
  echo "PASS: evaluator pipeline produced >=1 clean scored trial ($SCAN)"
  exit 0
fi
echo "FAIL: 0 clean scored trials — every trial errored or none completed ($SCAN)"
exit 1
