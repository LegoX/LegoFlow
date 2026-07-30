#!/usr/bin/env bash
# Root case 04: per-block smoke configs are present, parse, and are
# schema-compatible with their production subblock config.
# The root smoke (tests/smoke/run_pipeline.sh) overlays each
# tests/smoke/<block>/config.yaml onto subblock/<block>/config.yaml before
# launching, so a malformed or off-schema overlay would only blow up hours into
# the chain. Catch it here, cheaply.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$ROOT_DIR" <<'PY' || exit 1
import sys, os
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr); sys.exit(1)

root = sys.argv[1]
# The four blocks the root smoke chains.
BLOCKS = ["curator", "tracer", "trainer", "evaluator"]
errs = []
for b in BLOCKS:
    smoke = os.path.join(root, "tests", "smoke", b, "config.yaml")
    prod = os.path.join(root, "subblock", b, "config.yaml")
    if not os.path.isfile(smoke):
        errs.append(f"missing tests/smoke/{b}/config.yaml"); continue
    try:
        scfg = yaml.safe_load(open(smoke, encoding="utf-8")) or {}
    except Exception as e:
        errs.append(f"tests/smoke/{b}/config.yaml does not parse: {e}"); continue
    meta = scfg.get("meta_info") or {}
    if "meta_info" not in scfg or "runtime_info" not in scfg:
        errs.append(f"tests/smoke/{b}/config.yaml missing meta_info/runtime_info")
    if meta.get("name") != b:
        errs.append(f"tests/smoke/{b}/config.yaml: meta_info.name={meta.get('name')!r} != {b!r}")
    if meta.get("parent") != "swe_lego_live":
        errs.append(f"tests/smoke/{b}/config.yaml: parent={meta.get('parent')!r} != 'swe_lego_live'")
    # Schema-compat: the smoke's top-level keys must be a subset of production's
    # (smoke prunes prod fields it doesn't consume; it must not invent new
    # top-level sections the block code won't read).
    if os.path.isfile(prod):
        try:
            pcfg = yaml.safe_load(open(prod, encoding="utf-8")) or {}
        except Exception as e:
            errs.append(f"subblock/{b}/config.yaml does not parse: {e}"); continue
        extra = set(scfg) - set(pcfg)
        if extra:
            errs.append(f"tests/smoke/{b}/config.yaml has top-level keys absent from production: {sorted(extra)}")
        if b == "tracer":
            prod_sft = (((pcfg.get("runtime_info") or {}).get("input") or {}).get("sft_conversion") or {})
            smoke_sft = (((scfg.get("runtime_info") or {}).get("input") or {}).get("sft_conversion") or {})
            if not smoke_sft.get("tokenizer_name"):
                errs.append("tests/smoke/tracer/config.yaml missing required sft_conversion.tokenizer_name")
            elif smoke_sft["tokenizer_name"] != prod_sft.get("tokenizer_name"):
                errs.append("tests/smoke/tracer/config.yaml tokenizer_name differs from production")
            prod_pin = (((pcfg.get("meta_info") or {}).get("repositories") or {}).get("swe_data_process") or {}).get("commit")
            smoke_pin = (((scfg.get("meta_info") or {}).get("repositories") or {}).get("swe_data_process") or {}).get("commit")
            if smoke_pin != prod_pin:
                errs.append("tests/smoke/tracer/config.yaml swe_data_process pin differs from production")

if errs:
    for e in errs: print("FAIL:", e, file=sys.stderr)
    sys.exit(1)
print(f"PASS: smoke configs valid + schema-compatible ({', '.join(BLOCKS)})")
PY
