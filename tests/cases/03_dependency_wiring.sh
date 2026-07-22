#!/usr/bin/env bash
# Root case 03: inter-block dependency wiring resolves, both directions.
# Per BLOCK_DEFINITION.md, meta_info.dependencies has two keys, `from` and `to`.
# `from` (this block's own upstream): `<input.dot.path>: <block>.output.<key>`
# or the dict form `{from: <block>.output.<key>, when: {...}, required: bool}`.
# `to` (this block's own downstream, the mirror): `<output_key>: <block>.input.<path>`
# or the dict form `{to: <block>.input.<path>, when: {...}}`. Every `from` reference
# must name a real sibling block that declares that output key under
# runtime_info.output; every `to` reference must name a real sibling block that
# has that input path under runtime_info.input. A dangling reference means one
# side reads/sends a value the other end never emits/receives.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$ROOT_DIR" <<'PY' || exit 1
import sys, os
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr); sys.exit(1)

root = sys.argv[1]
BLOCKS = ["curator", "tracer", "trainer", "evaluator"]

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

def dot_get(mapping, dotted):
    node = mapping
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True

def input_path_exists(cfg, dotted):
    inp = (cfg.get("runtime_info") or {}).get("input") or {}
    return dot_get(inp, dotted)

errs = []
checked = 0
for b in BLOCKS:
    deps = (cfgs[b].get("meta_info") or {}).get("dependencies")
    if not isinstance(deps, dict) or set(deps.keys()) != {"from", "to"}:
        errs.append(f"subblock/{b}: meta_info.dependencies must have exactly `from` and `to` keys")
        continue

    for dep_key, dep_val in (deps.get("from") or {}).items():
        ref = dep_val.get("from") if isinstance(dep_val, dict) else dep_val
        label = f"meta_info.dependencies.from.{dep_key}"
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

    for out_key, to_val in (deps.get("to") or {}).items():
        ref = to_val.get("to") if isinstance(to_val, dict) else to_val
        label = f"meta_info.dependencies.to.{out_key}"
        if ref in (None, ""):
            continue
        if not isinstance(ref, str) or ".input." not in ref:
            errs.append(f"subblock/{b}: {label} = {ref!r} is not `<block>.input.<path>` (string or dict `to:`)")
            continue
        consumer, _, path = ref.partition(".input.")
        if consumer not in cfgs:
            errs.append(f"subblock/{b}: {label} -> unknown block {consumer!r}")
            continue
        if not input_path_exists(cfgs[consumer], path):
            errs.append(f"subblock/{b}: {label} -> {consumer}.input.{path} not declared")
            continue
        checked += 1

if errs:
    for e in errs: print("FAIL:", e, file=sys.stderr)
    sys.exit(1)
print(f"PASS: {checked} inter-block dependency reference(s) resolve (from + to)")
PY
