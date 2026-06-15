#!/usr/bin/env bash
# CI test 03: the sft uv env exists and the training stack imports.
# Mirrors install_env.sh's final import block: torch + swe_data_process +
# llamafactory must all import inside artifacts/env/lf. CUDA availability is
# reported but NOT required (the cases job may run on a CPU-only runner).

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

SFT_UV_REL="$(cfg meta_info.environment.sft_uv)"
[[ -n "$SFT_UV_REL" ]] || { echo "FAIL: meta_info.environment.sft_uv is empty"; exit 1; }
SFT_UV="$BLOCK_DIR/$SFT_UV_REL"
PY_BIN="$SFT_UV/bin/python"

[[ -x "$PY_BIN" ]] || { echo "FAIL: sft uv python missing at $SFT_UV_REL/bin/python — run /sft:setup"; exit 1; }

# Make the swe_data_process src importable even if the editable .pth drifted,
# exactly as train.sh does (PYTHONPATH=repos/swe_data_process/src).
SWE_DP_SRC="$BLOCK_DIR/repos/swe_data_process/src"

PYTHONPATH="$SWE_DP_SRC:${PYTHONPATH:-}" "$PY_BIN" - <<'PY'
import importlib, sys
mods = ["torch", "swe_data_process", "llamafactory"]
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:  # noqa: BLE001
        bad.append(f"{m}: {type(e).__name__}: {e}")
if bad:
    for line in bad:
        print(f"FAIL: import failed — {line}", file=sys.stderr)
    sys.exit(1)
import torch
print(f"INFO: torch {torch.__version__}  cuda_available={torch.cuda.is_available()}")
print("PASS: sft uv env imports torch + swe_data_process + llamafactory")
PY
