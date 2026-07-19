#!/usr/bin/env bash
# CI test 13: keep the smoke fixture aligned with production-critical pins and
# isolated from production runtime state.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$BLOCK_DIR/config.yaml" "$BLOCK_DIR/tests/smoke/config.yaml" <<'PY'
import sys
from pathlib import Path
import yaml

prod = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
smoke = yaml.safe_load(Path(sys.argv[2]).read_text()) or {}

prod_meta = prod["meta_info"]
smoke_meta = smoke["meta_info"]
assert (
    smoke_meta["repositories"]["harbor"]["commit"]
    == prod_meta["repositories"]["harbor"]["commit"]
), "smoke Harbor pin drift"

prod_input = prod["runtime_info"]["input"]
smoke_input = smoke["runtime_info"]["input"]
assert (
    smoke_input["agent"]["runtime_image"]
    == prod_input["agent"]["runtime_image"]
), "smoke runtime image drift"
assert smoke_input["harbor_job"]["jobs_dir"] == "artifacts/jobs/smoke"
assert smoke_input["harbor_job"]["n_tasks"] == 10
assert smoke_input["job_analysis"]["enabled"] is False
assert (
    smoke_input["litellm_proxy"]["port"]
    != prod_input["litellm_proxy"]["port"]
), "smoke LiteLLM port must be isolated from production"

output = smoke["runtime_info"]["output"]["eval_results_dir"]
assert "{agent,verifier}" in output["job_layout"]
assert output["results_summary_format"].endswith("/result.json")
PY

if grep -Fq 'docker ps --filter "name=harbor-trial-"' \
    "$BLOCK_DIR/tests/smoke/10_registry_task_demo.sh"; then
  echo "FAIL: smoke cleanup uses a broad/outdated Harbor container-name filter"
  exit 1
fi
for required in \
  'SMOKE_CONFIG="$SMOKE_TMP_DIR/config.yaml"' \
  'EVAL_CONFIG="$SMOKE_CONFIG"' \
  'date +%s >"$SMOKE_JOBS_DIR/.run-start"'; do
  grep -Fq "$required" "$BLOCK_DIR/tests/smoke/10_registry_task_demo.sh" || {
    echo "FAIL: smoke isolation contract missing: $required"
    exit 1
  }
done
if grep -Fq 'mv -f "$BACKUP" "$CONFIG"' "$BLOCK_DIR/tests/smoke/10_registry_task_demo.sh"; then
  echo "FAIL: smoke still rewrites/restores the tracked config in place"
  exit 1
fi

echo "PASS: smoke fixture pins, paths, port, and cleanup contract"
