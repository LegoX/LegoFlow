#!/usr/bin/env bash
# CI test 09: the merge smoke must execute the guarded training test, propagate
# failures, and keep remote infrastructure out of the tracked smoke fixture.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$(git -C "$BLOCK_DIR" rev-parse --show-toplevel)"
RUNNER="$REPO_ROOT/.github/scripts/sft_smoke_run.sh"
WORKFLOW="$REPO_ROOT/.github/workflows/ci.yml"

[[ -x "$RUNNER" ]] || { echo "FAIL: guarded SFT smoke runner is missing or not executable"; exit 1; }
for required in \
  'bash .github/scripts/sft_smoke_run.sh 2700' \
  'SFT_SMOKE_CI_STRICT=1' \
  'git reset --hard "$sha"' \
  'git submodule update --init --recursive --force --' \
  'bash tests/smoke/10_train_demo.sh'; do
  if ! grep -Fq "$required" "$WORKFLOW" "$RUNNER"; then
    echo "FAIL: SFT smoke contract missing: $required"
    exit 1
  fi
done

python3 - "$BLOCK_DIR/config.yaml" "$BLOCK_DIR/tests/smoke/config.yaml" <<'PY'
import sys
import yaml

prod = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
smoke = yaml.safe_load(open(sys.argv[2], encoding="utf-8")) or {}
for key in ("llama_factory", "legoflow_trace_crafter"):
    p = prod["meta_info"]["repositories"][key]["commit"]
    s = smoke["meta_info"]["repositories"][key]["commit"]
    assert p == s, f"{key} smoke pin {s} != production pin {p}"
resources = smoke["meta_info"]["resources"]
assert resources.get("ip") == "local", resources
assert not resources.get("key"), resources
assert not resources.get("user"), resources
PY

echo "PASS: guarded SFT smoke contract is isolated and failure-propagating"
