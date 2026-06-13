#!/usr/bin/env bash
# CI smoke 10: trajgen end-to-end on 10 tasks selected from the HuggingFace
# dataset configured in runtime_info.input.task_source (SWE-Lego/swerebenchv2-…).
#
# Strategy: swap config.yaml for a smoke variant (jobs_dir=artifacts/jobs/smoke,
# n_tasks=10, low concurrency, sft_conversion disabled), run prepare_tasks.sh
# to snapshot the HF dataset, then scripts/start.sh. Harbor selects the first
# 10 tasks from the HF snapshot (deterministic by dataset version +
# alphabetical task ID).
#
# Pass condition: at least one trial under artifacts/jobs/smoke/<job>/ produces
# a result.json whose verifier_result.rewards contains a positive value
# (i.e. ≥1 resolved trajectory). 30-minute wall-clock budget.
#
# This burns LLM tokens + Docker time. Only invoked when run.sh is called with
# --with-smoke. Not for cloud GitHub Actions runners.

set -uo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"
BACKUP="$BLOCK_DIR/tests/smoke/.config.yaml.backup.$$"
SMOKE_JOBS_DIR_REL="artifacts/jobs/smoke"
SMOKE_JOBS_DIR="$BLOCK_DIR/$SMOKE_JOBS_DIR_REL"

[[ -f "$CONFIG" ]] || { echo "FAIL: $CONFIG missing"; exit 1; }

cleanup() {
  local rc=$?
  if [[ -f "$BACKUP" ]]; then
    mv -f "$BACKUP" "$CONFIG"
    echo "INFO: restored config.yaml from backup"
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

cp -f "$CONFIG" "$BACKUP"

# Write the smoke config in place. Mutates only the fields needed; preserves
# repo pins, env paths, llm_api, task_source, etc.
SMOKE_JOBS_DIR_REL="$SMOKE_JOBS_DIR_REL" CONFIG="$CONFIG" python3 - <<'PY' || exit 1
import os, sys
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr); sys.exit(1)
path = os.environ["CONFIG"]
with open(path, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh) or {}
hj = cfg.setdefault("runtime_info", {}).setdefault("input", {}).setdefault("harbor_job", {})
hj["jobs_dir"] = os.environ["SMOKE_JOBS_DIR_REL"]
hj["n_concurrent"] = 2
hj["n_tasks"] = 10
hj["max_retries"] = 0
ag = cfg["runtime_info"]["input"].setdefault("agent", {})
ag["max_turns"] = 80
sft = cfg["runtime_info"]["input"].setdefault("sft_conversion", {})
sft["enabled"] = False
with open(path, "w", encoding="utf-8") as fh:
    yaml.safe_dump(cfg, fh, sort_keys=False)
print("INFO: smoke config written (n_tasks=10, n_concurrent=2, max_turns=80, jobs_dir="+hj["jobs_dir"]+")")
PY

# Fresh jobs dir for the smoke
rm -rf "$SMOKE_JOBS_DIR"
mkdir -p "$SMOKE_JOBS_DIR"

LOG="$BLOCK_DIR/artifacts/logs/smoke-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$(dirname "$LOG")"
echo "INFO: smoke log -> $LOG"

# prepare_tasks.sh must run before start.sh — start.sh's dryrun gate checks
# that artifacts/tasks/<dataset>/ is populated and refuses to launch otherwise.
echo "INFO: preparing tasks from HF dataset (see log)"
if ! bash "$BLOCK_DIR/scripts/prepare_tasks.sh" >>"$LOG" 2>&1; then
  echo "FAIL: prepare_tasks.sh exited non-zero — see $LOG"
  tail -30 "$LOG" 2>/dev/null || true
  exit 1
fi

set +e
timeout --foreground 1800 bash "$BLOCK_DIR/scripts/start.sh" >>"$LOG" 2>&1
rc=$?
set -e

# Tail the log no matter what — gives the human a starting point on failure.
echo "--- last 20 log lines ---"
tail -20 "$LOG" 2>/dev/null || true
echo "--------------------------"

# Count resolved trials.
RESOLVED="$(SMOKE_JOBS_DIR="$SMOKE_JOBS_DIR" python3 - <<'PY'
import json, os, glob, sys
root = os.environ["SMOKE_JOBS_DIR"]
total, resolved, parse_err = 0, 0, 0
for path in glob.glob(os.path.join(root, "*", "*", "result.json")):
    total += 1
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        parse_err += 1; continue
    vr = data.get("verifier_result")
    if not isinstance(vr, dict):
        continue
    rewards = vr.get("rewards")
    if isinstance(rewards, dict) and any(
        isinstance(v, (int, float)) and v > 0 for v in rewards.values()
    ):
        resolved += 1
print(f"{resolved}/{total} (parse_errors={parse_err})")
PY
)"
echo "INFO: trial result.json scan: $RESOLVED"

resolved_count="${RESOLVED%%/*}"
if [[ "${resolved_count:-0}" -ge 1 ]]; then
  echo "PASS: at least 1 resolved trajectory ($RESOLVED)"
  exit 0
fi

if [[ "$rc" == 124 ]]; then
  echo "FAIL: 30-minute wall-clock budget exceeded; no resolved trajectory ($RESOLVED)"
else
  echo "FAIL: start.sh finished (rc=$rc) with 0 resolved trajectories ($RESOLVED)"
fi
exit 1
