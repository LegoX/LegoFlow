#!/usr/bin/env bash
# CI test runner for the ROOT block (swe_lego_live).
#
# Always runs the cheap root cases (pytest + tests/cases/*.sh). The smoke is
# opt-in and SELECTABLE — pick the isolated smoke of one subblock, every
# subblock's isolated smoke, or the root CHAINED end-to-end smoke:
#
#   bash tests/run.sh                      # cheap cases only (no net/GPU/Docker)
#   bash tests/run.sh --smoke root         # the chained pipeline (curator->tracer
#                                          #   ->trainer->evaluator, real wiring, fail-fast)
#   bash tests/run.sh --with-smoke         # alias for --smoke root
#   bash tests/run.sh --smoke curator       # ONE subblock's isolated smoke
#   bash tests/run.sh --smoke subblocks    # every subblock's isolated smoke, in turn
#
# Root-chain options (only meaningful with --smoke root), forwarded verbatim to
# tests/smoke/run_pipeline.sh:
#   --from <stage> --to <stage>   run a sub-range of the chain (stages:
#                                 curator tracer trainer evaluator)
#   --budget <sec>                per-stage wait budget override
#
# Other:
#   --smoke-only                  skip the cheap cases, run only the smoke
#
# Distinction: a SUBBLOCK smoke is isolated (fixed fixtures, one block); the
# ROOT smoke chains all four blocks, feeding each block's real output into the
# next. "Run the root smoke" therefore exercises every block end-to-end, which
# is different from "run each subblock's smoke" independently.
#
# Each cheap case exits 0=PASS, 77=SKIP, anything else=FAIL.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBBLOCKS=(curator tracer trainer evaluator)

SMOKE_TARGET=""                          # "", root, <subblock>, subblocks/all
SMOKE_FROM=""; SMOKE_TO=""
SMOKE_BUDGET="${ROOT_SMOKE_BUDGET:-}"
CASES=1
[[ "${TESTS_WITH_SMOKE:-0}" == "1" ]] && SMOKE_TARGET="root"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-smoke)  SMOKE_TARGET="root"; shift ;;
    --smoke)       SMOKE_TARGET="${2:?--smoke needs a target: root|<subblock>|subblocks}"; shift 2 ;;
    --from)        SMOKE_FROM="${2:?}"; shift 2 ;;
    --to)          SMOKE_TO="${2:?}"; shift 2 ;;
    --budget)      SMOKE_BUDGET="${2:?}"; shift 2 ;;
    --smoke-only)  CASES=0; shift ;;
    -h|--help)     sed -n '2,33p' "$0"; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

pass=0; fail=0; skip=0
failed_names=()

tally() {  # tally <rc> <label>
  case "$1" in
    0)  pass=$((pass+1)); echo "[PASS] $2" ;;
    77) skip=$((skip+1)); echo "[SKIP] $2" ;;
    *)  fail=$((fail+1)); failed_names+=("$2 (rc=$1)"); echo "[FAIL] $2 (rc=$1)" ;;
  esac
}

run_one() {  # run_one <script> <group>
  local path="$1" group="$2" name rc start_ts dur
  name="$(basename "$path")"
  echo "================================================================="
  echo ">> $group/$name"
  echo "================================================================="
  start_ts=$(date +%s)
  set +e; bash "$path"; rc=$?; set -e
  dur=$(( $(date +%s) - start_ts ))
  tally "$rc" "$group/$name (${dur}s)"
  echo
}

run_root_smoke() {
  local args=()
  [[ -n "$SMOKE_FROM" ]]   && args+=(--from "$SMOKE_FROM")
  [[ -n "$SMOKE_TO" ]]     && args+=(--to "$SMOKE_TO")
  [[ -n "$SMOKE_BUDGET" ]] && args+=(--budget "$SMOKE_BUDGET")
  echo "================================================================="
  echo ">> smoke: ROOT chained pipeline — run_pipeline.sh ${args[*]}"
  echo "================================================================="
  local rc
  set +e; bash "$ROOT_DIR/tests/smoke/run_pipeline.sh" "${args[@]}"; rc=$?; set -e
  tally "$rc" "smoke/root-chain"
  echo
}

run_subblock_smoke() {  # run_subblock_smoke <block>
  local b="$1" runner="$ROOT_DIR/subblock/$1/tests/run.sh" rc
  echo "================================================================="
  echo ">> smoke: subblock/$b (isolated) — tests/run.sh --with-smoke"
  echo "================================================================="
  if [[ ! -x "$runner" && ! -f "$runner" ]]; then
    tally 77 "smoke/$b (no tests/run.sh)"; echo; return
  fi
  set +e; bash "$runner" --with-smoke; rc=$?; set -e
  tally "$rc" "smoke/$b"
  echo
}

# --- cheap cases ----------------------------------------------------------------
if [[ "$CASES" == "1" ]]; then
  echo "================================================================="
  echo ">> pytest/test_root_block.py"
  echo "================================================================="
  # Prefer python3 -m pytest: a bare `pytest` on PATH may belong to python2.
  if python3 -m pytest --version >/dev/null 2>&1; then
    set +e; python3 -m pytest "$ROOT_DIR/tests/test_root_block.py" -q; rc=$?; set -e
    tally "$rc" "pytest/test_root_block.py"
  elif command -v pytest >/dev/null 2>&1; then
    set +e; pytest "$ROOT_DIR/tests/test_root_block.py" -q; rc=$?; set -e
    tally "$rc" "pytest/test_root_block.py"
  else
    skip=$((skip+1)); echo "[SKIP] pytest not installed"
  fi
  echo
  for f in "$ROOT_DIR"/tests/cases/*.sh; do
    [[ -f "$f" ]] || continue
    run_one "$f" cases
  done
fi

# --- smoke (selectable) ---------------------------------------------------------
case "$SMOKE_TARGET" in
  "")
    echo "INFO: smoke skipped. Choose one with:"
    echo "       --smoke root        (chained end-to-end: all 4 blocks)"
    echo "       --smoke <subblock>  (one of: ${SUBBLOCKS[*]})"
    echo "       --smoke subblocks   (each subblock's isolated smoke)"
    ;;
  root)
    run_root_smoke ;;
  subblocks|all)
    for b in "${SUBBLOCKS[@]}"; do run_subblock_smoke "$b"; done ;;
  curator|tracer|trainer|evaluator|rl)
    run_subblock_smoke "$SMOKE_TARGET" ;;
  *)
    echo "ERROR: unknown --smoke target '$SMOKE_TARGET' (want: root | ${SUBBLOCKS[*]} | subblocks)" >&2
    exit 2 ;;
esac

echo "================================================================="
echo "  root tests: PASS=$pass  SKIP=$skip  FAIL=$fail"
echo "================================================================="
if [[ "$fail" -gt 0 ]]; then
  for n in "${failed_names[@]}"; do echo "  FAILED: $n"; done
  exit 1
fi
exit 0
