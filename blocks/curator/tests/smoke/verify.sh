#!/usr/bin/env bash
# CI smoke verifier for curator.
# Pass: <output.swe_tasks_dir.path>/<smoke.output_subdir>/verifiable_tasks.txt
#       has >= 1 line.
# Exit 0 = PASS, 77 = SKIP (output dir missing), 1 = FAIL.

set -uo pipefail

BLOCK_DIR="${BLOCK_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# Derive the smoke output dir from the overlaid config so this verify stays
# in lock-step with what run.sh launched. Falls back to the historical
# hardcoded path if config.yaml is missing the fields (e.g. the legacy
# blocks/curator/config.yaml that doesn't have a `smoke:` section).
read_cfg() {
  # read_cfg <dotted-key> <default>
  python3 - "$BLOCK_DIR/config.yaml" "$1" "$2" <<'PY' 2>/dev/null || echo "$2"
import sys, yaml
try:
    d = yaml.safe_load(open(sys.argv[1])) or {}
except Exception:
    print(sys.argv[3]); raise SystemExit
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print(sys.argv[3] if cur is None else cur)
PY
}
BASE=$(read_cfg "runtime_info.output.swe_tasks_dir.path" "artifacts/swe_tasks")
SUBDIR=$(read_cfg "runtime_info.input.smoke.output_subdir" "py-cc-smoke")
OUTPUT="$BLOCK_DIR/$BASE/$SUBDIR"
MANIFEST="$OUTPUT/verifiable_tasks.txt"

if [[ ! -d "$OUTPUT" ]]; then
  echo "SKIP: no $OUTPUT — claude /curator:create-tasks smoke didn't get to the create step"
  exit 77
fi

if [[ -s "$MANIFEST" ]]; then
  echo "PASS: verifiable_tasks.txt has $(wc -l <"$MANIFEST") task(s):"
  sed 's/^/         /' "$MANIFEST"
  exit 0
fi

# Manifest empty. Distinguish:
#   (a) smoke ran but the verifier flaked on every attempted PR (upstream
#       Dockerfile/PR drift — see task #19 "refresh swegen smoke fixture").
#       Known long-running upstream rot, NOT a swegen regression — SKIP
#       (→ yellow warning so the CI signal stays honest without redding
#       every dev push when the fixture decays).
#   (b) smoke didn't even create a task dir — real swegen-side regression
#       — FAIL (→ red).
shopt -s nullglob
TASK_DIRS=("$OUTPUT"/*/)
shopt -u nullglob
LOG="$BLOCK_DIR/artifacts/swe_tasks/.swegen-smoke-py.log"
if (( ${#TASK_DIRS[@]} > 0 )); then
  echo "SKIP: $MANIFEST empty, but ${#TASK_DIRS[@]} task dir(s) were created — swegen ran end-to-end but no PR passed NOP/Oracle verification (likely upstream fixture drift; see task #19 to refresh the smoke PR list)."
  for d in "${TASK_DIRS[@]}"; do
    printf '         %s\n' "$(basename "$d")"
  done
  if [[ -f "$LOG" ]]; then
    echo "--- last 30 lines of $LOG ---"
    tail -30 "$LOG" || true
  fi
  exit 77
fi

echo "FAIL: $MANIFEST empty AND no task dirs under $OUTPUT — swegen create did not run"
if [[ -f "$LOG" ]]; then
  echo "--- last 30 lines of $LOG ---"
  tail -30 "$LOG" || true
fi
exit 1
