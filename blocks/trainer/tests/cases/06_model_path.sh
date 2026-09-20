#!/usr/bin/env bash
# CI test 06: the base model directory is present and looks like a HF model.
# Asserts model.model_name_or_path exists and contains config.json. SKIPs when
# absent — the base model is a large external asset that may not be staged on
# the cases runner (a real /trainer:run requires it).

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

MODEL_RAW="$(cfg runtime_info.input.model.model_name_or_path)"
[[ -n "$MODEL_RAW" ]] || { echo "FAIL: model.model_name_or_path is empty"; exit 1; }

if [[ "$MODEL_RAW" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
  echo "SKIP: Hugging Face Hub model ID configured; remote availability is checked at train time: $MODEL_RAW"
  exit 77
fi

case "$MODEL_RAW" in
  /*) MODEL_DIR="$MODEL_RAW" ;;
  *)  MODEL_DIR="$BLOCK_DIR/$MODEL_RAW" ;;
esac

if [[ ! -d "$MODEL_DIR" ]]; then
  echo "SKIP: model dir not found on this host: $MODEL_DIR"
  exit 77
fi

if [[ -f "$MODEL_DIR/config.json" ]]; then
  echo "INFO: model=$MODEL_DIR"
  echo "PASS: base model directory present (config.json found)"
else
  echo "FAIL: $MODEL_DIR exists but has no config.json — not a usable HF model dir"
  exit 1
fi
