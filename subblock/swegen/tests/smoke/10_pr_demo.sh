#!/usr/bin/env bash
# CI smoke 10: end-to-end with 10 Python PRs from the quick-verify fixture.
# Pass condition: at least one task ID lands in verifiable_tasks.txt.
# Wall-clock budget: ~60 min, capped by `timeout` below.
#
# Concurrency hedges against flaky PRs: individual fixture PRs upstream-flake
# over time (Dockerfile/test drift, missing verifier reward.txt), and with
# --n-concurrent 1 a single bad PR at the head of the list burns its full
# --cc-timeout serially before the next PR even starts (observed: one PR ate
# ~36 min of cc-timeout retries before a good PR ran). Running several PRs in
# parallel + --max-pr 1 means the first PR to verify wins and we stop, so a
# flaky head-of-list PR no longer gates wall-clock. The fixture is also ordered
# known-good first, flaky demoted to the end (kept in sync with the curated CI
# list in tests/smoke/config.yaml -> runtime_info.input.smoke.input_prs).
#
# This burns LLM tokens + Docker time. Only invoked when run.sh is called with
# --with-smoke (or TESTS_WITH_SMOKE=1). Not for cloud GitHub Actions runners.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$BLOCK_DIR/scripts/load_runtime_env.sh"
load_runtime_env >/dev/null 2>&1 || true

: "${DOCKER_HOST:=unix:///var/run/docker.sock}"
export DOCKER_HOST

VENV_BIN="$BLOCK_DIR/artifacts/envs/swegen-env/bin"
[[ -x "$VENV_BIN/swegen" ]] || { echo "FAIL: $VENV_BIN/swegen missing — run /swegen:setup"; exit 1; }

FIXTURE="$BLOCK_DIR/tests/smoke/fixtures/python_pr_ids.txt"
[[ -s "$FIXTURE" ]] || { echo "FAIL: missing fixture $FIXTURE"; exit 1; }

OUTPUT="$BLOCK_DIR/artifacts/swe_tasks/py-cc-smoke"
STATE="$BLOCK_DIR/artifacts/swe_tasks/.swegen-smoke-py"
LOG="$BLOCK_DIR/artifacts/swe_tasks/.swegen-smoke-py.log"

mkdir -p "$OUTPUT" "$STATE"
: > "$LOG"

# If config.yaml selects cc_provider_mode: openai_proxy, swegen's Claude Code
# verification path needs a local LiteLLM proxy on cc_proxy_port. Bring it up
# (no-op for native mode) and tear it down on exit; otherwise CC verification
# fails silently and no task ever lands in verifiable_tasks.txt.
# shellcheck source=/dev/null
source "$BLOCK_DIR/scripts/cc_proxy_lib.sh"
trap cc_proxy_stop EXIT
cc_proxy_start "$BLOCK_DIR" "$BLOCK_DIR/config.yaml" \
  || { echo "FAIL: CC LiteLLM proxy did not start (openai_proxy mode)"; exit 1; }

echo "INFO: running swegen create on 10 PRs (output=$OUTPUT)"
echo "INFO: log -> $LOG"

# --max-pr 1 → bail as soon as one PR verifies. --n-concurrent 5 races several
# PRs so a flaky head-of-list PR can't serialize the whole budget (matches the
# CI smoke config's empirical sweet spot). Walltime cap 60m via `timeout`.
set +e
timeout --foreground 3600 \
  "$VENV_BIN/swegen" create \
    --input-ids-file "$FIXTURE" \
    --max-pr 1 \
    --n-concurrent 5 \
    --output "$OUTPUT" \
    --state-dir "$STATE" \
    --timeout 2400 \
    --cc-timeout 1800 \
    --no-require-issue \
    --min-source-files 1 \
    --max-source-files 10 \
    --docker-prune-batch 0 \
    --verbose \
    >"$LOG" 2>&1
rc=$?
set -e

MANIFEST="$OUTPUT/verifiable_tasks.txt"
if [[ -s "$MANIFEST" ]]; then
  echo "PASS: verifiable_tasks.txt has $(wc -l <"$MANIFEST") task(s):"
  sed 's/^/         /' "$MANIFEST"
  exit 0
fi

if [[ "$rc" == 124 ]]; then
  echo "FAIL: 60-minute wall-clock budget exceeded; no verified task produced"
else
  echo "FAIL: swegen create finished (rc=$rc) but verifiable_tasks.txt is empty"
fi
echo "--- last 40 log lines ---"
tail -40 "$LOG" || true
exit 1
