#!/usr/bin/env bash
# CI smoke 10: evaluator end-to-end on a tiny slice of the configured benchmark.
#
# Strategy: render an isolated smoke variant and pass it through EVAL_CONFIG
# (jobs_dir=artifacts/jobs/smoke, n_tasks=10, n_concurrent=5, max_retries=0,
# agent.max_turns=50), then run scripts/start.sh. The tracked config is never
# rewritten. evaluator is registry-driven, so there is NO prepare_tasks step
# — Harbor resolves the configured dataset from registry.json and caps it to
# n_tasks (so this smoke runs the first 10 tasks of whatever task_source.
# dataset_name is set to — e.g. swebench-verified).
#
# Pass condition (a *pipeline-health* signal, not a model-quality gate): at
# least one trial reaches a terminal **clean scored** state — the verifier ran
# and recorded a reward (0 OR 1) AND that trial did NOT raise an agent
# exception. We require "clean" rather than merely "scored" because a broken
# agent (e.g. the runtime bind-mount is missing, so the in-container install
# 403s and the agent exits nonzero) still leaves the repo unchanged and the
# verifier still records reward 0 — so "a reward exists" would mask exactly the
# failure case 08 guards against. Requiring one trial that scored *without*
# erroring proves proxy + agent-in-container + verifier all work end-to-end.
# Resolved (reward>0) is reported but does not gate: at this scale a capable
# model can legitimately solve 0 SWE-bench tasks without the pipeline being
# broken. (tracer's smoke gates on ≥1 *resolved* because a resolved trajectory
# is tracer's product; evaluator's product is a scored result.)
#
# Liveness note: Harbor writes `stats.evals` (reward_stats / exception_stats)
# incrementally during the run, but only populates `trial_results[]` at job
# finalization. We scan `stats.evals` so a timeout-killed run is still scored
# from the trials that finished. 40-minute wall-clock budget (10 tasks).
#
# This burns LLM tokens + Docker time. Only invoked when run.sh is called with
# --with-smoke. Not for cloud GitHub Actions runners.

set -uo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE_CONFIG="${EVAL_CONFIG:-$BLOCK_DIR/config.yaml}"
SMOKE_TMP_DIR=""
SMOKE_CONFIG=""
SMOKE_JOBS_DIR_REL="artifacts/jobs/smoke"
SMOKE_JOBS_DIR="$BLOCK_DIR/$SMOKE_JOBS_DIR_REL"
SMOKE_LITELLM_PORT="${SMOKE_LITELLM_PORT:-4102}"

[[ -f "$SOURCE_CONFIG" ]] || { echo "FAIL: $SOURCE_CONFIG missing"; exit 1; }
command -v flock >/dev/null 2>&1 || { echo "FAIL: flock is required for smoke isolation"; exit 1; }
mkdir -p "$BLOCK_DIR/artifacts"
exec 9>"$BLOCK_DIR/artifacts/.smoke.lock"
flock -n 9 || { echo "FAIL: another eval smoke is already running"; exit 1; }

cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  # Kill only Harbor containers whose mounts point into this smoke jobs dir.
  # A broad `name=harbor-trial-` kill can interrupt an unrelated eval sharing
  # the same Docker daemon.
  if command -v docker >/dev/null 2>&1; then
    while IFS= read -r cid; do
      [[ -n "$cid" ]] || continue
      mounts="$(docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "$cid" 2>/dev/null || true)"
      if grep -Fq "$SMOKE_JOBS_DIR" <<<"$mounts"; then
        docker kill "$cid" >/dev/null 2>&1 || true
      fi
    done < <(docker ps --format '{{.ID}}' 2>/dev/null)
    # Trial containers run as root and drop files under <trial>/agent with
    # mode 700; reclaim ownership so the runner user (and upload-artifact) can
    # walk the tree.
    if [ -d "$SMOKE_JOBS_DIR" ]; then
      host_uid="$(id -u)"
      host_gid="$(id -g)"
      docker run --rm -v "$SMOKE_JOBS_DIR:/x:rw" alpine:3 \
        sh -c "chown -R $host_uid:$host_gid /x 2>/dev/null; chmod -R u+rwX /x 2>/dev/null" || true
    fi
  fi
  if [[ -n "$SMOKE_TMP_DIR" && -d "$SMOKE_TMP_DIR" ]]; then
    rm -rf "$SMOKE_TMP_DIR"
  fi
  return "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

SMOKE_TMP_DIR="$(mktemp -d "$BLOCK_DIR/artifacts/.smoke-config.XXXXXX")"
SMOKE_CONFIG="$SMOKE_TMP_DIR/config.yaml"
cp -f "$SOURCE_CONFIG" "$SMOKE_CONFIG"

# Write an isolated smoke config. The tracked config is never rewritten, so
# comments and concurrent production runs cannot be affected.
SMOKE_JOBS_DIR_REL="$SMOKE_JOBS_DIR_REL" SMOKE_LITELLM_PORT="$SMOKE_LITELLM_PORT" CONFIG="$SMOKE_CONFIG" python3 - <<'PY' || exit 1
import os, sys
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr); sys.exit(1)
path = os.environ["CONFIG"]
with open(path, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh) or {}
runtime = cfg.setdefault("runtime_info", {})
inputs = runtime.setdefault("input", {})
hj = inputs.setdefault("harbor_job", {})
hj["jobs_dir"] = os.environ["SMOKE_JOBS_DIR_REL"]
hj["n_concurrent"] = 5
hj["n_tasks"] = 10
hj["max_retries"] = 0
inputs.setdefault("job_analysis", {})["enabled"] = False
inputs.setdefault("litellm_proxy", {})["port"] = int(os.environ["SMOKE_LITELLM_PORT"])
ag = inputs.setdefault("agent", {})
ag["max_turns"] = 50
out = runtime.setdefault("output", {}).setdefault("eval_results_dir", {})
root = os.environ["SMOKE_JOBS_DIR_REL"]
out["path"] = root
out["job_layout"] = f"{root}/<job>/<task>/{{agent,verifier}}/"
out["trajectory_format"] = f"{root}/<job>/<task>/agent/litellm-trajectory.jsonl"
out["results_summary_format"] = f"{root}/<job>/result.json"
with open(path, "w", encoding="utf-8") as fh:
    yaml.safe_dump(cfg, fh, sort_keys=False)
print(f"INFO: smoke config written (n_tasks=10, n_concurrent=5, max_turns=50, port={inputs['litellm_proxy']['port']}, jobs_dir={hj['jobs_dir']})")
PY

# Fresh jobs dir for the smoke
rm -rf "$SMOKE_JOBS_DIR"
mkdir -p "$SMOKE_JOBS_DIR"
date +%s >"$SMOKE_JOBS_DIR/.run-start"

LOG="$BLOCK_DIR/artifacts/logs/smoke-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$(dirname "$LOG")"
echo "INFO: smoke log -> $LOG"

# Warm cpfs/networked-FS cache: the first `harbor --help` import takes ~20 s on a
# cold gpufs mount (lots of pydantic/asyncio modules to page in). Warming it here
# keeps the preflight and smoke startup latency predictable.
echo "INFO: warming harbor CLI cache"
"$BLOCK_DIR/artifacts/env/harbor-uv/bin/harbor" --help >/dev/null 2>&1 || true

# LiteLLM proxy: force a single uvicorn worker for the smoke. Multi-worker
# mode uses gunicorn with a 30s worker-boot timeout; on a cold cpfs cache the
# litellm[proxy] import takes >30s, so every worker is SIGKILL'd, the
# supervisor crashloops, the TCP port is briefly open during each restart (so
# start.sh's readiness probe passes) but trial containers find no live worker
# and report "litellm.InternalServerError: Connection error". Single-worker
# mode skips gunicorn entirely → no timeout, slow imports are fine.
# Propagates through start.sh → setsid → serve_litellm.sh (which defaults to 4).
export LITELLM_NUM_WORKERS=1

set +e
# 2400 s budget for 10 trials + --kill-after 60 s so SIGKILL fires if start.sh
# ignores SIGTERM (Harbor pipes the signal up the bash chain unreliably).
EVAL_CONFIG="$SMOKE_CONFIG" \
  timeout --foreground --kill-after=60s 2400 bash "$BLOCK_DIR/scripts/start.sh" >>"$LOG" 2>&1
rc=$?
set -e

echo "--- last 20 log lines ---"
tail -20 "$LOG" 2>/dev/null || true
echo "--------------------------"

# Scan the newest smoke job's result.json live `stats.evals`:
#   CLEAN    = trials that got a verifier reward AND did not raise an exception
#   EVAL     = trials that got any verifier reward (incl. ones that also errored)
#   RESOLVED = trials with a positive reward
#   ERRORED  = trials with an agent exception
# CLEAN is the gate; the rest are diagnostics.
SCAN="$(SMOKE_JOBS_DIR="$SMOKE_JOBS_DIR" python3 - <<'PY'
import json, os, glob
root = os.environ["SMOKE_JOBS_DIR"]
results = sorted(glob.glob(os.path.join(root, "*", "result.json")), key=os.path.getmtime)
if not results:
    print("CLEAN:0 EVAL:0 RESOLVED:0 ERRORED:0 TOTAL:0 (no result.json)")
    raise SystemExit
try:
    with open(results[-1], encoding="utf-8") as fh:
        data = json.load(fh)
except Exception as e:
    print(f"CLEAN:0 EVAL:0 RESOLVED:0 ERRORED:0 TOTAL:0 (parse_error:{e})")
    raise SystemExit
stats = data.get("stats") or {}
evals = stats.get("evals") or {}
evaluated, resolved, errored = set(), set(), set()
for ds in evals.values():
    if not isinstance(ds, dict):
        continue
    for by_value in (ds.get("reward_stats") or {}).values():
        for value, names in (by_value or {}).items():
            try:
                positive = float(value) > 0
            except (TypeError, ValueError):
                positive = False
            for n in names or []:
                evaluated.add(n)
                if positive:
                    resolved.add(n)
    for names in (ds.get("exception_stats") or {}).values():
        for n in names or []:
            errored.add(n)
clean = evaluated - errored
total = data.get("n_total_trials") or stats.get("n_trials") or 0
print(f"CLEAN:{len(clean)} EVAL:{len(evaluated)} RESOLVED:{len(resolved)} ERRORED:{len(errored)} TOTAL:{total}")
PY
)"
echo "INFO: result.json scan: $SCAN"

clean="$(sed -n 's/.*CLEAN:\([0-9]*\).*/\1/p' <<<"$SCAN")"
clean="${clean:-0}"

if [[ "$clean" -ge 1 ]]; then
  echo "PASS: evaluator pipeline produced >=1 clean scored trial ($SCAN)"
  exit 0
fi

if [[ "$rc" == 124 ]]; then
  echo "FAIL: 40-minute wall-clock budget exceeded; no clean scored trial ($SCAN)"
else
  echo "FAIL: start.sh finished (rc=$rc) with 0 clean scored trials — every trial errored or none completed ($SCAN). Inspect stats.evals[*].exception_stats in the newest artifacts/jobs/smoke/<job>/result.json"
fi
exit 1
