#!/usr/bin/env bash
# Root case 03: inter-block dependency wiring resolves.
# Per BLOCK_DEFINITION.md, each consumer block declares its upstream in its own
# meta_info.dependencies: `<input.dot.path>: <block>.output.<key>` or the dict
# form `{from: <block>.output.<key>, when: {...}, required: bool}`. Every such
# reference must name a real sibling block that actually declares that output
# key under runtime_info.output. A dangling reference means a downstream block
# reads a value its producer never emits.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$ROOT_DIR" <<'PY' || exit 1
import sys, os
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr); sys.exit(1)

root = sys.argv[1]
BLOCKS = ["curator", "tracer", "trainer", "rl", "evaluator"]

cfgs = {}
for b in BLOCKS:
    p = os.path.join(root, "subblock", b, "config.yaml")
    try:
        cfgs[b] = yaml.safe_load(open(p, encoding="utf-8")) or {}
    except Exception as e:
        print(f"FAIL: cannot parse subblock/{b}/config.yaml: {e}", file=sys.stderr); sys.exit(1)

def output_keys(cfg):
    out = (cfg.get("runtime_info") or {}).get("output") or {}
    return set(out.keys()) if isinstance(out, dict) else set()

def collect_deps(cfg):
    """Yield (label, ref) for every dependency reference in a block config."""
    meta = cfg.get("meta_info") or {}
    deps = []
    md = meta.get("dependencies")
    if isinstance(md, dict):
        for k, v in md.items():
            deps.append((f"meta_info.dependencies.{k}", v))
    subs = meta.get("subblocks")
    if isinstance(subs, dict):
        for child, spec in subs.items():
            cd = (spec or {}).get("dependencies") if isinstance(spec, dict) else None
            if isinstance(cd, dict):
                for k, v in cd.items():
                    deps.append((f"meta_info.subblocks.{child}.dependencies.{k}", v))
    return deps

errs = []
checked = 0
for b in BLOCKS:
    for label, ref in collect_deps(cfgs[b]):
        if isinstance(ref, dict):
            ref = ref.get("from")
        if ref in (None, ""):
            continue
        if not isinstance(ref, str) or ".output." not in ref:
            errs.append(f"subblock/{b}: {label} = {ref!r} is not `<block>.output.<key>` (string or dict `from:`)")
            continue
        src_block, _, rest = ref.partition(".output.")
        out_key = rest.split(".")[0]
        if src_block not in cfgs:
            errs.append(f"subblock/{b}: {label} -> unknown block {src_block!r}")
            continue
        if out_key not in output_keys(cfgs[src_block]):
            errs.append(f"subblock/{b}: {label} -> {src_block}.output.{out_key} not declared "
                        f"(has: {sorted(output_keys(cfgs[src_block]))})")
            continue
        checked += 1

if errs:
    for e in errs: print("FAIL:", e, file=sys.stderr)
    sys.exit(1)
print(f"PASS: {checked} inter-block dependency reference(s) resolve to declared outputs")
PY
