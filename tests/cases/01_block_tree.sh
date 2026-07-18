#!/usr/bin/env bash
# Root case 01: block-tree structural integrity.
# Every expected subblock exists, its config.yaml parses, meta_info.name
# matches the directory name, and parent == swe_lego_live.
# (Shell mirror of tests/test_root_block.py so `tests/run.sh` is self-contained
#  on a runner without pytest.)

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$ROOT_DIR" <<'PY' || exit 1
import sys, os
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr); sys.exit(1)

root = sys.argv[1]
EXPECTED = ["curator", "tracer", "trainer", "rl", "evaluator"]
PARENT = "swe_lego_live"
errs = []
for name in EXPECTED:
    d = os.path.join(root, "subblock", name)
    cfg_path = os.path.join(d, "config.yaml")
    if not os.path.isdir(d):
        errs.append(f"missing subblock dir: subblock/{name}"); continue
    if not os.path.isfile(cfg_path):
        errs.append(f"missing subblock/{name}/config.yaml"); continue
    try:
        cfg = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
    except Exception as e:
        errs.append(f"subblock/{name}/config.yaml does not parse: {e}"); continue
    meta = cfg.get("meta_info") or {}
    if "meta_info" not in cfg or "runtime_info" not in cfg:
        errs.append(f"subblock/{name}/config.yaml missing meta_info/runtime_info")
    if meta.get("name") != name:
        errs.append(f"subblock/{name}: meta_info.name={meta.get('name')!r} != {name!r}")
    if meta.get("parent") != PARENT:
        errs.append(f"subblock/{name}: meta_info.parent={meta.get('parent')!r} != {PARENT!r}")

if errs:
    for e in errs: print("FAIL:", e, file=sys.stderr)
    sys.exit(1)
print(f"PASS: block tree OK ({', '.join(EXPECTED)})")
PY
