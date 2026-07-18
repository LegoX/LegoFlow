#!/usr/bin/env bash
# CI test 11: clean/archive helpers preserve durable outputs and record job
# metadata without touching the block's real artifacts directory.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

ARTIFACTS="$TMP_DIR/artifacts"
mkdir -p "$ARTIFACTS"/{env,archives,jobs,datasets,runtime,logs,litellm}
printf 'runs: []\n' >"$ARTIFACTS/index.yaml"

EVAL_ARTIFACTS_DIR="$ARTIFACTS" bash "$BLOCK_DIR/scripts/clean.sh" >/dev/null
for durable in env archives jobs datasets runtime; do
  [[ -d "$ARTIFACTS/$durable" ]] || {
    echo "FAIL: clean.sh removed durable $durable/"; exit 1;
  }
done
for disposable in logs litellm; do
  [[ ! -e "$ARTIFACTS/$disposable" ]] || {
    echo "FAIL: clean.sh kept disposable $disposable/"; exit 1;
  }
done
set +e
EVAL_ARTIFACTS_DIR=/ bash "$BLOCK_DIR/scripts/clean.sh" --dry-run >/dev/null 2>&1
unsafe_clean_rc=$?
set -e
[[ "$unsafe_clean_rc" == "2" ]] || {
  echo "FAIL: clean.sh accepted unsafe EVAL_ARTIFACTS_DIR=/ (rc=$unsafe_clean_rc)"; exit 1;
}
exec 8>"$ARTIFACTS/.smoke.lock"
flock -n 8
set +e
EVAL_ARTIFACTS_DIR="$ARTIFACTS" bash "$BLOCK_DIR/scripts/clean.sh" --dry-run >/dev/null 2>&1
active_smoke_clean_rc=$?
set -e
flock -u 8
[[ "$active_smoke_clean_rc" == "2" ]] || {
  echo "FAIL: clean.sh ran while smoke lock was held (rc=$active_smoke_clean_rc)"; exit 1;
}

EVAL_ARTIFACTS_DIR="$ARTIFACTS" \
EVAL_JOB_DIR="$BLOCK_DIR/artifacts/jobs/example" \
  bash "$BLOCK_DIR/scripts/archive_run.sh" 0 2026-07-17T00:00:00Z test-note \
  >/dev/null 2>&1

archive_pids=()
for i in {2..7}; do
  EVAL_ARTIFACTS_DIR="$ARTIFACTS" \
  EVAL_JOB_DIR="$BLOCK_DIR/artifacts/jobs/example-$i" \
    bash "$BLOCK_DIR/scripts/archive_run.sh" 0 "2026-07-17T00:00:0${i}Z" "parallel-$i" \
    >/dev/null 2>&1 &
  archive_pids+=("$!")
done
for pid in "${archive_pids[@]}"; do
  wait "$pid"
done

python3 - "$ARTIFACTS" <<'PY'
import sys
from pathlib import Path
import yaml

root = Path(sys.argv[1])
index = yaml.safe_load((root / "index.yaml").read_text())
runs = index["runs"]
assert len(runs) == 7, runs
assert len({run["id"] for run in runs}) == 7, runs
assert all((root / "archives" / run["id"]).is_dir() for run in runs), runs
run = runs[0]
assert run["id"] == "run_001", run
assert run["status"] == "completed", run
assert run["job_dir"] == "artifacts/jobs/example", run
assert run["notes"] == "test-note", run

metadata = yaml.safe_load(
    (root / "archives" / "run_001" / "metadata.yaml").read_text()
)
assert metadata["job_dir"] == "artifacts/jobs/example", metadata
PY

echo "PASS: clean preserves durable artifacts and archive records job metadata"
