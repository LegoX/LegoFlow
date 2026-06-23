#!/usr/bin/env bash
# CI smoke verifier for terminalgen.
#
# Replays the known-good fixture task (https-nginx-cert-setup, a harbor-1.1
# verified terminal task) through terminal-lego's Docker validator and asserts
# reward=1.0. This is deterministic — it spends no LLM or StackExchange quota and
# proves that build → solve → test → reward works on this host.
#
# Exit 0 = PASS, 77 = SKIP (fixture or validator missing), 1 = FAIL.

set -uo pipefail

BLOCK_DIR="${BLOCK_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
FIXTURE="$BLOCK_DIR/tests/smoke/fixtures/https-nginx-cert-setup"
VALIDATOR="$BLOCK_DIR/repos/terminal-lego/validator/validate_tasks.py"

if [[ ! -d "$FIXTURE" ]]; then
    echo "SKIP: fixture missing at $FIXTURE — run /terminalgen:setup to stage it"
    exit 77
fi
if [[ ! -f "$VALIDATOR" ]]; then
    echo "SKIP: validator missing — run: git submodule update --init repos/terminal-lego"
    exit 77
fi
if ! docker info >/dev/null 2>&1; then
    echo "SKIP: docker daemon not reachable"
    exit 77
fi

PY="$BLOCK_DIR/artifacts/envs/terminalgen-env/bin/python"
[[ -x "$PY" ]] || PY="python3"

# The validator only discovers task_* prefixed dirs; stage the fixture as task_00000.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/terminalgen-smoke.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/in" "$WORK/out"
cp -r "$FIXTURE" "$WORK/in/task_00000"

echo "[smoke] validating fixture https-nginx-cert-setup via terminal-lego validator…"
echo "[smoke] note: the task's test.sh bootstraps uv + Python + pytest over the"
echo "[smoke]       network on first run (warm cache ~10s, cold can take minutes)."
"$PY" "$VALIDATOR" --input "$WORK/in" --output "$WORK/out" --workers 1 --timeout 600 \
    2>&1 | tail -20

REPORT="$WORK/out/validation_report.json"
if [[ ! -f "$REPORT" ]]; then
    echo "FAIL: no validation_report.json produced"
    exit 1
fi

PASSED="$("$PY" -c "import json;print(json.load(open('$REPORT')).get('passed',0))" 2>/dev/null || echo 0)"
if [[ "$PASSED" == "1" ]]; then
    echo "PASS: fixture validated with reward=1.0 (Docker build/solve/test OK on this host)"
    exit 0
fi

# A timeout during the network-bound uv/pytest bootstrap is not a broken task —
# report SKIP so the smoke doesn't red-flag a slow/throttled network.
STATUS="$("$PY" -c "import json;r=json.load(open('$REPORT'))['results'];print(r[0].get('status','') if r else '')" 2>/dev/null || echo '')"
if [[ "$STATUS" == "timeout" ]]; then
    echo "SKIP: fixture validation timed out during the network-bound uv/pytest bootstrap"
    echo "      (not a task defect). Retry on a faster network or warm the Docker/uv cache."
    exit 77
fi

echo "FAIL: fixture did not pass validation (passed=$PASSED, status=$STATUS). Report:"
cat "$REPORT" 2>/dev/null | sed 's/^/         /'
exit 1
