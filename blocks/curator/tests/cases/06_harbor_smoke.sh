#!/usr/bin/env bash
# CI test 06: Harbor NOP=0 / Oracle=1 against a known verified task.
# SKIP (exit 77) if the fixture task isn't present locally — the smoke is
# meaningful only when the verified task is already on disk.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$BLOCK_DIR/scripts/load_runtime_env.sh"
load_runtime_env >/dev/null 2>&1 || true

DATASET_ROOT="$BLOCK_DIR/artifacts/swe_tasks/py-cc"
TASK_ID="tox-dev__tox-3813"
JOBS_DIR="$BLOCK_DIR/artifacts/swe_tasks/.legoflow-curator/harbor-jobs-cases-test"
VENV_BIN="$BLOCK_DIR/artifacts/envs/legoflow-curator-env/bin"

if [[ ! -d "$DATASET_ROOT/$TASK_ID" ]]; then
  echo "SKIP: $DATASET_ROOT/$TASK_ID not present — no Harbor smoke fixture"
  exit 77
fi
[[ -x "$VENV_BIN/legoflow-curator" ]] || { echo "FAIL: $VENV_BIN/legoflow-curator missing — run /curator:setup"; exit 1; }

: "${DOCKER_HOST:=unix:///var/run/docker.sock}"
export DOCKER_HOST

OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT

if ! "$VENV_BIN/legoflow-curator" validate "$DATASET_ROOT" \
    --task "$TASK_ID" \
    --jobs-dir "$JOBS_DIR" \
    --env docker >"$OUT" 2>&1; then
  echo "FAIL: legoflow-curator validate exited non-zero"
  tail -40 "$OUT"
  exit 1
fi

if grep -qE 'NOP[[:space:]]+reward=0' "$OUT" && grep -qE 'Oracle[[:space:]]+reward=1' "$OUT"; then
  echo "PASS: Harbor smoke ($TASK_ID): NOP=0, Oracle=1"
else
  echo "FAIL: expected 'NOP reward=0' and 'Oracle reward=1' in output:"
  tail -30 "$OUT"
  exit 1
fi
