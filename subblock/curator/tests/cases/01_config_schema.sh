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


# meta_info.dependencies must be {from: {...}, to: {...}} (both keys, both mappings).
deps = get(cfg, "meta_info.dependencies")
if (
    not isinstance(deps, dict)
    or set(deps.keys()) != {"from", "to"}
    or not isinstance(deps.get("from"), dict)
    or not isinstance(deps.get("to"), dict)
):
    print("FAIL: meta_info.dependencies must have exactly `from` and `to` keys, each a mapping (use {} for no edges)", file=sys.stderr)
    sys.exit(1)
name = get(cfg, "meta_info.name")
if name != "curator":
    print(f"FAIL: meta_info.name == {name!r}, expected 'curator'", file=sys.stderr)
    sys.exit(1)

# Per-language config (mirrors /curator:check Step 1).
EXPECTED_LANGS = ["py", "js", "ts", "go", "c", "cpp", "java", "rust"]
langs = get(cfg, "runtime_info.input.languages") or {}
missing_langs = [l for l in EXPECTED_LANGS if l not in langs]
if missing_langs:
    print(f"FAIL: runtime_info.input.languages missing keys: {missing_langs}", file=sys.stderr)
    sys.exit(1)

import os
block_dir = os.path.dirname(os.path.abspath(cfg_path))
bad = []
for lang in EXPECTED_LANGS:
    lc = langs.get(lang) or {}
    if not lc.get("enabled"):
        continue
    params = lc.get("params") or {}
    for p in ("timeout", "cc_timeout", "n_concurrent"):
        if params.get(p) is None:
            bad.append(f"{lang}.params.{p} missing")
    script_path = os.path.join(block_dir, "scripts", f"create_{lang}.sh")
    if not os.path.isfile(script_path):
        bad.append(f"scripts/create_{lang}.sh missing")
if bad:
    for line in bad:
        print(f"FAIL: {line}", file=sys.stderr)
    sys.exit(1)

print("PASS: config.yaml schema")
PY

# Shared block-contract validator (schema-only: `human` fill markers are
# expected on a fresh clone and downgraded to warnings here).
REPO_ROOT="$(cd "$BLOCK_DIR/../.." && pwd)"
if [[ -f "$REPO_ROOT/scripts/validate_config.py" ]]; then
  python3 "$REPO_ROOT/scripts/validate_config.py" --block "$BLOCK_DIR" --config "$CONFIG" --schema-only \
    || { echo "FAIL: validate_config.py reported schema failures"; exit 1; }
else
  echo "WARN: shared validator not found — skipping contract validation"
fi
