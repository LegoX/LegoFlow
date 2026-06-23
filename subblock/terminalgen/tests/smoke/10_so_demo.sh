#!/usr/bin/env bash
# CI smoke 10: real end-to-end on a tiny SO sample (no scraping).
# Generates terminal tasks from the 2-question fixture, Docker-validates them,
# and passes if at least one task verifies (reward=1.0).
# Wall-clock budget: a few minutes. Burns a little LLM + Docker time.
#
# Only invoked when run.sh is called with --with-smoke (or TESTS_WITH_SMOKE=1).
# Requires OPENAI_API_KEY / OPENAI_API_BASE_URL / MODEL_NAME and Docker.
#
# Note: this is the *generation* smoke. The cheaper, deterministic
# `verify.sh` (fixture replay, no LLM) is the default first check.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$BLOCK_DIR/scripts/load_runtime_env.sh"
load_runtime_env >/dev/null 2>&1 || true

: "${DOCKER_HOST:=unix:///var/run/docker.sock}"
export DOCKER_HOST

PY="$BLOCK_DIR/artifacts/envs/terminalgen-env/bin/python"
[[ -x "$PY" ]] || PY="python3"

TL="$BLOCK_DIR/repos/terminal-lego"
[[ -f "$TL/generator/task_generator.py" ]] || { echo "FAIL: $TL missing — git submodule update --init"; exit 1; }

FIXTURE="$BLOCK_DIR/tests/smoke/fixtures/so_data_sample.json"
[[ -s "$FIXTURE" ]] || { echo "FAIL: missing fixture $FIXTURE"; exit 1; }
[[ -n "${OPENAI_API_BASE_URL:-}" ]] || { echo "FAIL: OPENAI_API_BASE_URL unset"; exit 1; }

OUTBASE="$BLOCK_DIR/artifacts/experiments/quick-verify"
CAND="$OUTBASE/_candidates"
OUT="$OUTBASE/validated"
LOG="$OUTBASE/smoke.log"
mkdir -p "$CAND" "$OUT"
: > "$LOG"

echo "INFO: generating from 2-question fixture (output=$CAND)"
echo "INFO: log -> $LOG"

set +e
timeout --foreground 1800 \
  "$PY" "$TL/generator/task_generator.py" \
    --input "$FIXTURE" \
    --output "$CAND" \
    --workers 1 \
    --limit 1 \
    --api-base "$OPENAI_API_BASE_URL" \
    --model "$MODEL_NAME" \
    >>"$LOG" 2>&1
gen_rc=$?

timeout --foreground 900 \
  "$PY" "$TL/validator/validate_tasks.py" \
    --input "$CAND" \
    --output "$OUT" \
    --workers 1 \
    --timeout 300 \
    >>"$LOG" 2>&1
val_rc=$?
set -e

PASSED="$("$PY" -c "import json,glob;
rep=glob.glob('$OUT/validation_report.json')
print(json.load(open(rep[0])).get('passed',0) if rep else 0)" 2>/dev/null || echo 0)"

if [[ "$PASSED" -ge 1 ]]; then
  echo "PASS: $PASSED task(s) generated and verified end-to-end"
  exit 0
fi

echo "FAIL: no task verified (gen_rc=$gen_rc val_rc=$val_rc). Last 40 log lines:"
tail -40 "$LOG" || true
exit 1
