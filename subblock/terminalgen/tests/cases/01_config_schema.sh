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
    "meta_info.repos.terminal-lego",
    "meta_info.repos.terminal-lego.commit_id",
    "runtime_info.input.so_api_key",
    "runtime_info.input.llm_api.api_key",
    "runtime_info.input.llm_api.api_base_url",
    "runtime_info.input.llm_api.gen_model",
    "runtime_info.output.terminal_tasks_dir.path",
    "runtime_info.output.merged_tasks_dir.path",
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
if name != "terminalgen":
    print(f"FAIL: meta_info.name == {name!r}, expected 'terminalgen'", file=sys.stderr)
    sys.exit(1)

# Per-domain config (mirrors /terminalgen:check Step 1).
EXPECTED_DOMAINS = [
    "core-terminal-os", "versioning-containers", "networking-services",
    "file-text-processing", "python-ecosystem", "ml-data", "databases-storage",
    "web-automation-apis", "security-cryptography", "debugging-reliability",
    "algorithms-concurrency", "media-scientific", "build-editor-tooling",
]
domains = get(cfg, "runtime_info.input.domains") or {}
missing_domains = [d for d in EXPECTED_DOMAINS if d not in domains]
if missing_domains:
    print(f"FAIL: runtime_info.input.domains missing keys: {missing_domains}", file=sys.stderr)
    sys.exit(1)

bad = []
for dom in EXPECTED_DOMAINS:
    dc = domains.get(dom) or {}
    if not dc.get("enabled"):
        continue
    if not dc.get("tag_filter"):
        bad.append(f"{dom}.tag_filter missing/empty")
    params = dc.get("params") or {}
    for p in ("gen_workers", "val_workers", "val_timeout"):
        if params.get(p) is None:
            bad.append(f"{dom}.params.{p} missing")
if bad:
    for line in bad:
        print(f"FAIL: {line}", file=sys.stderr)
    sys.exit(1)

print("PASS: config.yaml schema")
PY
