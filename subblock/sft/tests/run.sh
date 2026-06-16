#!/usr/bin/env bash
# CI test runner for the sft block — deterministic cases only.
#
# Usage:
#   bash tests/run.sh        # run every cases/NN_*.sh
#
# Cases exit 0=PASS, 77=SKIP, anything else=FAIL.

set -uo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for arg in "$@"; do
  case "$arg" in
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

pass=0; fail=0; skip=0
failed_names=()

run_one() {
  local path="$1"
  local name
  name="$(basename "$path")"
  local label="$2/$name"
  echo "================================================================="
  echo ">> $label"
  echo "================================================================="
  local start_ts end_ts
  start_ts=$(date +%s)
  set +e
  bash "$path"
  local rc=$?
  set -e
  end_ts=$(date +%s)
  local dur=$((end_ts - start_ts))
  case "$rc" in
    0)  pass=$((pass+1)); echo "[PASS] $label (${dur}s)" ;;
    77) skip=$((skip+1)); echo "[SKIP] $label (${dur}s)" ;;
    *)  fail=$((fail+1)); failed_names+=("$label (rc=$rc, ${dur}s)"); echo "[FAIL] $label (rc=$rc, ${dur}s)" ;;
  esac
  echo
}

for f in "$BLOCK_DIR"/tests/cases/*.sh; do
  [[ -f "$f" ]] || continue
  run_one "$f" cases
done

echo "================================================================="
echo "  sft tests: PASS=$pass  SKIP=$skip  FAIL=$fail"
echo "================================================================="
if [[ "$fail" -gt 0 ]]; then
  for n in "${failed_names[@]}"; do echo "  FAILED: $n"; done
  exit 1
fi
exit 0
