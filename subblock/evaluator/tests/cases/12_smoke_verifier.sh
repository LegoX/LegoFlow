#!/usr/bin/env bash
# CI test 12: smoke verifier distinguishes no output, clean scored trials, and
# scored trials that also raised agent exceptions.
set -euo pipefail

BLOCK_DIR_REAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
VERIFY="$BLOCK_DIR_REAL/tests/smoke/verify.sh"
JOBS="$TMP_DIR/artifacts/jobs/smoke"

run_verify() {
  set +e
  BLOCK_DIR="$TMP_DIR" bash "$VERIFY" >/dev/null
  local rc=$?
  set -e
  printf '%s\n' "$rc"
}

rc="$(run_verify)"
[[ "$rc" == "1" ]] || { echo "FAIL: missing smoke output returned rc=$rc, expected 1"; exit 1; }

mkdir -p "$JOBS/job"
rc="$(run_verify)"
[[ "$rc" == "1" ]] || { echo "FAIL: missing run marker returned rc=$rc, expected 1"; exit 1; }
date +%s >"$JOBS/.run-start"
cat >"$JOBS/job/result.json" <<'JSON'
{
  "n_total_trials": 1,
  "stats": {
    "evals": {
      "eval": {
        "reward_stats": {"reward": {"0.0": ["trial"]}},
        "exception_stats": {}
      }
    }
  }
}
JSON
rc="$(run_verify)"
[[ "$rc" == "0" ]] || { echo "FAIL: clean scored trial returned rc=$rc, expected 0"; exit 1; }

echo "$(( $(date +%s) + 60 ))" >"$JOBS/.run-start"
rc="$(run_verify)"
[[ "$rc" == "1" ]] || { echo "FAIL: stale result returned rc=$rc, expected 1"; exit 1; }
date +%s >"$JOBS/.run-start"
cat >"$JOBS/job/result.json" <<'JSON'
{
  "n_total_trials": 1,
  "stats": {
    "evals": {
      "eval": {
        "reward_stats": {"reward": {"0.0": ["trial"]}},
        "exception_stats": {"AgentError": ["trial"]}
      }
    }
  }
}
JSON
rc="$(run_verify)"
[[ "$rc" == "1" ]] || { echo "FAIL: errored scored trial returned rc=$rc, expected 1"; exit 1; }

echo "PASS: smoke verifier requires at least one clean scored trial"
