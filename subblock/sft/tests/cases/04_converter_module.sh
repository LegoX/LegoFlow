#!/usr/bin/env bash
# CI test 04: the trajectory converter for the configured scaffold is importable.
# Mirrors train.sh's scaffold -> CONVERTER_MODULE mapping, then resolves the
# module inside the sft uv env (with PYTHONPATH=repos/swe_data_process/src, as
# train.sh runs it). Catches a scaffold/converter mismatch before STEP 0.

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

SCAFFOLD="$(cfg runtime_info.input.source.scaffold)"

# Keep this case statement in lock-step with scripts/train.sh.
case "$SCAFFOLD" in
  openhands-sdk) MODULE="swe_data_process.openhands.convert_openhands_sdk_to_im" ;;
  claude-code)   MODULE="swe_data_process.claudecode_opencode.convert_cc_to_im" ;;
  open-code)     MODULE="swe_data_process.claudecode_opencode.convert_oc_to_im" ;;
  terminus2)     MODULE="swe_data_process.terminus2.convert_terminus2_to_im" ;;
  *)
    echo "FAIL: unsupported scaffold '$SCAFFOLD' (expected openhands-sdk | claude-code | open-code | terminus2)"
    exit 1
    ;;
esac

SFT_UV_REL="$(cfg meta_info.environment.sft_uv)"
PY_BIN="$BLOCK_DIR/$SFT_UV_REL/bin/python"
[[ -x "$PY_BIN" ]] || { echo "FAIL: sft uv python missing at $SFT_UV_REL/bin/python — run /sft:setup"; exit 1; }

SWE_DP_SRC="$BLOCK_DIR/repos/swe_data_process/src"

if PYTHONPATH="$SWE_DP_SRC:${PYTHONPATH:-}" "$PY_BIN" -c "import importlib; importlib.import_module('$MODULE')" >/dev/null 2>&1; then
  echo "INFO: scaffold=$SCAFFOLD -> $MODULE"
  echo "PASS: converter module importable"
else
  echo "FAIL: converter module not importable: $MODULE (scaffold=$SCAFFOLD)"
  exit 1
fi
