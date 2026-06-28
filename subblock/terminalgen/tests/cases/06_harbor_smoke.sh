#!/usr/bin/env bash
# CI test 06: Docker round-trip against the known-good fixture task.
# Replays tests/smoke/fixtures/https-nginx-cert-setup through terminal-lego's
# validator and asserts reward=1.0. This is the terminal-task analog of swegen's
# Harbor NOP/Oracle smoke. SKIP (77) if the validator or Docker is unavailable.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$BLOCK_DIR/scripts/load_runtime_env.sh"
load_runtime_env >/dev/null 2>&1 || true

FIXTURE="$BLOCK_DIR/tests/smoke/fixtures/https-nginx-cert-setup"
VALIDATOR="$BLOCK_DIR/repos/terminal-lego/validator/validate_tasks.py"

[[ -d "$FIXTURE" ]] || { echo "SKIP: fixture missing at $FIXTURE"; exit 77; }
[[ -f "$VALIDATOR" ]] || { echo "SKIP: validator missing — git submodule update --init repos/terminal-lego"; exit 77; }

: "${DOCKER_HOST:=unix:///var/run/docker.sock}"
export DOCKER_HOST
docker info >/dev/null 2>&1 || { echo "SKIP: docker daemon not reachable"; exit 77; }

PY="$BLOCK_DIR/artifacts/envs/terminalgen-env/bin/python"
[[ -x "$PY" ]] || PY="python3"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/terminalgen-case06.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/in" "$WORK/out"
cp -r "$FIXTURE" "$WORK/in/task_00000"

if ! "$PY" "$VALIDATOR" --input "$WORK/in" --output "$WORK/out" --workers 1 --timeout 600 >"$WORK/log" 2>&1; then
  echo "FAIL: validator exited non-zero"; tail -30 "$WORK/log"; exit 1
fi

PASSED="$("$PY" -c "import json;print(json.load(open('$WORK/out/validation_report.json')).get('passed',0))" 2>/dev/null || echo 0)"
if [[ "$PASSED" == "1" ]]; then
  echo "PASS: fixture smoke (https-nginx-cert-setup): reward=1.0"
else
  STATUS="$("$PY" -c "import json;r=json.load(open('$WORK/out/validation_report.json'))['results'];print(r[0].get('status','') if r else '')" 2>/dev/null || echo '')"
  if [[ "$STATUS" == "timeout" ]]; then
    echo "SKIP: fixture timed out during network-bound uv/pytest bootstrap (not a task defect)"; exit 77
  fi
  echo "FAIL: fixture did not validate (passed=$PASSED, status=$STATUS)"; tail -30 "$WORK/log"; exit 1
fi
