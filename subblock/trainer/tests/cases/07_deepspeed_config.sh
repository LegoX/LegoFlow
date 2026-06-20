#!/usr/bin/env bash
# CI test 07: the DeepSpeed config referenced by training.deepspeed exists and
# is valid. Guards the regression where the ds_z*_config.json files under
# artifacts/training_config/deepspeed/ get deleted but config.yaml still points
# at one — train.sh would then fail at launch. Asserts the file parses as JSON
# and declares a zero_optimization stage.

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

DS_RAW="$(cfg runtime_info.input.training.deepspeed)"
[[ -n "$DS_RAW" ]] || { echo "FAIL: training.deepspeed is empty"; exit 1; }

case "$DS_RAW" in
  /*) DS_PATH="$DS_RAW" ;;
  *)  DS_PATH="$BLOCK_DIR/$DS_RAW" ;;
esac

[[ -f "$DS_PATH" ]] || { echo "FAIL: deepspeed config missing: $DS_RAW (restore artifacts/training_config/deepspeed/)"; exit 1; }

DS_PATH="$DS_PATH" python3 - <<'PY' || exit 1
import json, os, sys
path = os.environ["DS_PATH"]
try:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
except Exception as e:  # noqa: BLE001
    print(f"FAIL: {path} is not valid JSON: {e}", file=sys.stderr)
    sys.exit(1)
zo = cfg.get("zero_optimization")
if not isinstance(zo, dict) or "stage" not in zo:
    print(f"FAIL: {path} has no zero_optimization.stage", file=sys.stderr)
    sys.exit(1)
print(f"INFO: deepspeed config OK (ZeRO stage {zo['stage']}): {path}")
print("PASS: deepspeed config present and valid")
PY
