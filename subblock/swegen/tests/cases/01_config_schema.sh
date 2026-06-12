#!/usr/bin/env bash
# CI test 01: config.yaml schema.
# Asserts config.yaml parses and the keys required by the runtime contract
# are present and non-empty. Calibrated to this CI runner's expected layout.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

[[ -f "$CONFIG" ]] || { echo "FAIL: $CONFIG missing"; exit 1; }

python3 - "$CONFIG" <<'PY' || exit 1
import sys
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed in the runner's python3", file=sys.stderr)
    sys.exit(1)

cfg_path = sys.argv[1]
with open(cfg_path, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh) or {}

REQUIRED = [
    "meta_info.name",
    "meta_info.environment.venv_path",
    "meta_info.environment.requirements",
    "meta_info.repos.swegen",
    "runtime_info.input.github_tokens",
    "runtime_info.input.llm_api.api_key",
    "runtime_info.input.llm_api.api_base_url",
    "runtime_info.input.llm_api.pr_model",
    "runtime_info.input.llm_api.task_model",
    "runtime_info.output.swe_tasks_dir.path",
]

def get(d, dotted):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur

missing = [k for k in REQUIRED if get(cfg, k) in (None, "")]
if missing:
    for k in missing:
        print(f"FAIL: missing or empty: {k}", file=sys.stderr)
    sys.exit(1)

name = get(cfg, "meta_info.name")
if name != "swegen":
    print(f"FAIL: meta_info.name == {name!r}, expected 'swegen'", file=sys.stderr)
    sys.exit(1)

print("PASS: config.yaml schema")
PY
