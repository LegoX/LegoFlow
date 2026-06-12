#!/usr/bin/env bash
# CI test 08: consumption_ledger.yaml parses and every done/failed/skipped
# entry's task_id appears in environment.extra.HARBOR_EXCLUDE_TASKS.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LEDGER="$BLOCK_DIR/artifacts/consumption_ledger.yaml"
CONFIG="$BLOCK_DIR/config.yaml"

[[ -f "$LEDGER" ]] || { echo "FAIL: $LEDGER missing — initialise with: printf 'description: %s\\nruns: []\\n' \"trajgen ledger\" > '$LEDGER'"; exit 1; }

LEDGER_PATH="$LEDGER" CONFIG_PATH="$CONFIG" python3 - <<'PY' || exit 1
import os, sys
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed in runner python3", file=sys.stderr); sys.exit(1)

doc = yaml.safe_load(open(os.environ["LEDGER_PATH"], encoding="utf-8"))
cfg = yaml.safe_load(open(os.environ["CONFIG_PATH"], encoding="utf-8")) or {}
exclude_raw = (cfg.get("environment",{}).get("extra",{}).get("HARBOR_EXCLUDE_TASKS") or "")
exclude = set(exclude_raw.split())

if not isinstance(doc, dict) or not isinstance(doc.get("runs"), list):
    print("FAIL: ledger must be a mapping with a 'runs' list", file=sys.stderr); sys.exit(1)

valid_status = {"pending", "running", "done", "failed", "skipped"}
bad, leak = [], []
for i, e in enumerate(doc["runs"]):
    if not isinstance(e, dict):
        bad.append((i, "<not_a_mapping>")); continue
    st = e.get("status")
    if st not in valid_status:
        bad.append((e.get("task_id", f"#{i}"), st)); continue
    if st in {"done", "failed", "skipped"}:
        tid = e.get("task_id")
        if tid and tid not in exclude:
            leak.append((tid, st))

if bad:
    print(f"FAIL: {len(bad)} ledger entries have invalid status", file=sys.stderr)
    for tid, st in bad[:10]: print(f"       {tid}: {st}", file=sys.stderr)
    sys.exit(1)
if leak:
    print(f"FAIL: {len(leak)} done/failed/skipped tasks not in HARBOR_EXCLUDE_TASKS — Harbor would re-run them", file=sys.stderr)
    for tid, st in leak[:10]: print(f"       {tid} ({st})", file=sys.stderr)
    sys.exit(1)
print(f"PASS: ledger consistent ({len(doc['runs'])} runs; HARBOR_EXCLUDE_TASKS covers all terminal entries)")
PY
