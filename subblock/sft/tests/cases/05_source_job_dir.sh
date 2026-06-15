#!/usr/bin/env bash
# CI test 05: the configured trajectory source exists and holds raw trajectories.
# Asserts source.job_dir contains at least one
#   <task>/agent/litellm-trajectory.jsonl
# (the format train.sh's STEP 0 converts). SKIPs when the dir is absent — the
# trajgen output may live on another node / not be staged into this runner.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

cfg() { python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

JOB_DIR_RAW="$(cfg runtime_info.input.source.job_dir)"
[[ -n "$JOB_DIR_RAW" ]] || { echo "FAIL: source.job_dir is empty"; exit 1; }

# Resolve relative job_dir against the block dir (train.sh's abspath rule).
case "$JOB_DIR_RAW" in
  /*) JOB_DIR="$JOB_DIR_RAW" ;;
  *)  JOB_DIR="$BLOCK_DIR/$JOB_DIR_RAW" ;;
esac

if [[ ! -d "$JOB_DIR" ]]; then
  echo "SKIP: source.job_dir not found on this host: $JOB_DIR"
  echo "      (trajgen output may live elsewhere; conversion would fail without it)"
  exit 77
fi

N_TRAJ="$(find "$JOB_DIR" -maxdepth 3 -name 'litellm-trajectory.jsonl' 2>/dev/null | head -200 | wc -l | tr -d ' ')"
if [[ "${N_TRAJ:-0}" -ge 1 ]]; then
  echo "INFO: job_dir=$JOB_DIR"
  echo "PASS: source.job_dir has $N_TRAJ raw trajectory file(s)"
else
  echo "FAIL: source.job_dir exists but has no */agent/litellm-trajectory.jsonl: $JOB_DIR"
  exit 1
fi
