#!/usr/bin/env bash
# CI test 07: the configured benchmark resolves in Harbor's registry.json.
# eval is registry-driven (no local task staging), so the producer→consumer
# contract is "the (dataset_name, version) pair exists in registry.json and
# Harbor can expand it to N tasks". Mirrors dryrun.sh section 8 resolution.

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

PROVIDER="$(cfg runtime_info.input.task_source.provider)"
DATASET_NAME="$(cfg runtime_info.input.task_source.dataset_name)"
DATASET_VERSION="$(cfg runtime_info.input.task_source.version)"
REGISTRY_RAW="$(cfg runtime_info.input.task_source.registry_path)"
HARBOR_PATH_RAW="$(cfg meta_info.repositories.harbor.path)"

[[ "$PROVIDER" == "harbor_registry" ]] || { echo "FAIL: task_source.provider must be harbor_registry (got '$PROVIDER')"; exit 1; }
[[ -n "$DATASET_NAME" ]] || { echo "FAIL: task_source.dataset_name is empty"; exit 1; }

if [[ -z "$REGISTRY_RAW" && -n "$HARBOR_PATH_RAW" ]]; then
  REGISTRY_RAW="$HARBOR_PATH_RAW/registry.json"
fi
[[ -n "$REGISTRY_RAW" ]] || { echo "FAIL: registry_path not configured (and harbor.path empty)"; exit 1; }
if [[ "$REGISTRY_RAW" = /* ]]; then REGISTRY_ABS="$REGISTRY_RAW"; else REGISTRY_ABS="$BLOCK_DIR/$REGISTRY_RAW"; fi
[[ -f "$REGISTRY_ABS" ]] || { echo "FAIL: registry.json not found at $REGISTRY_RAW — run scripts/update_repos.sh"; exit 1; }

RESULT="$(python3 - "$REGISTRY_ABS" "$DATASET_NAME" "$DATASET_VERSION" <<'PY'
import json, sys
from pathlib import Path
registry_path, dataset_name, version = sys.argv[1:4]
try:
    entries = json.loads(Path(registry_path).read_text(encoding="utf-8"))
except Exception as exc:
    print(f"invalid:{exc}"); sys.exit(0)
if not isinstance(entries, list):
    print("unexpected_shape"); sys.exit(0)
match = None
for e in entries:
    if not isinstance(e, dict): continue
    if e.get("name") != dataset_name: continue
    if version and e.get("version") != version: continue
    match = e; break
if match is None:
    print("not_found"); sys.exit(0)
n = len(match.get("tasks", []) or [])
print(f"ok:{match.get('name')}@{match.get('version')}:{n}")
PY
)"

case "$RESULT" in
  ok:*)
    IFS=':' read -r _ matched ntasks <<<"$RESULT"
    if [[ "${ntasks:-0}" -ge 1 ]]; then
      echo "PASS: registry entry resolved: $matched ($ntasks tasks)"
    else
      echo "FAIL: registry entry $matched resolved but expands to 0 tasks"; exit 1
    fi
    ;;
  not_found)
    echo "FAIL: ${DATASET_NAME}@${DATASET_VERSION} not in registry.json — check the curated table in CLAUDE.md"; exit 1 ;;
  invalid:*)
    echo "FAIL: registry.json could not be parsed: ${RESULT#invalid:}"; exit 1 ;;
  *)
    echo "FAIL: registry inspection inconclusive: $RESULT"; exit 1 ;;
esac
