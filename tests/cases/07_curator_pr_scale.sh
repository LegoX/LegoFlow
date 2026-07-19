#!/usr/bin/env bash
# Root case 07: curator smoke is sized for the 200-PR from-scratch collection.
# The design collects ~200 PRs and generates tasks (focusing on the verified
# ones). Validate the collection knobs are coherent — a target far below 200, a
# missing language, or a zero stop-after would quietly shrink the run.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$ROOT_DIR" <<'PY' || exit 1
import sys, os
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr); sys.exit(1)

cfg = yaml.safe_load(open(os.path.join(sys.argv[1], "tests/smoke/curator/config.yaml"), encoding="utf-8")) or {}
def get(dotted, default=None):
    cur = cfg
    for p in dotted.split("."):
        if not isinstance(cur, dict): return default
        cur = cur.get(p)
    return default if cur is None else cur

errs = []
collect = get("runtime_info.input.smoke.collect") or {}
if collect.get("enabled") is not True:
    errs.append("smoke.collect.enabled must be true (collect from scratch)")
langs = collect.get("languages")
if not langs or "python" not in str(langs):
    errs.append(f"smoke.collect.languages={langs!r}, expected to include python")
target = collect.get("target_prs")
if not isinstance(target, int) or target < 100:
    errs.append(f"smoke.collect.target_prs={target!r}, expected ~200")
# repo_num * max_prs_per_repo must be able to supply target_prs.
rn, mpr = collect.get("repo_num"), collect.get("max_prs_per_repo")
if isinstance(rn, int) and isinstance(mpr, int) and isinstance(target, int) and rn * mpr < target:
    errs.append(f"smoke.collect: repo_num*max_prs_per_repo={rn*mpr} < target_prs={target}")

# Task-generation knobs must be present and positive.
for k in ("timeout", "cc_timeout", "n_concurrent"):
    v = get(f"runtime_info.input.languages.py.params.{k}")
    if not isinstance(v, int) or v <= 0:
        errs.append(f"languages.py.params.{k}={v!r}, expected a positive int")

# max_pr = stop after N verified tasks. Root smoke wants a handful so tracer
# has >1 task to infer on (1 makes reward==1 a coin-flip).
max_pr = get("runtime_info.input.smoke.max_pr")
if not isinstance(max_pr, int) or max_pr < 1:
    errs.append(f"smoke.max_pr={max_pr!r}, expected a positive int (verified-task stop count)")

if errs:
    for e in errs: print("FAIL:", e, file=sys.stderr)
    sys.exit(1)
print(f"PASS: curator smoke sized for from-scratch collection (target_prs={target}, max_pr={max_pr})")
PY
