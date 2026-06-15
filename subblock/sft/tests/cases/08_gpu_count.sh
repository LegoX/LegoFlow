#!/usr/bin/env bash
# CI test 08: enough GPUs are visible for the configured training topology.
# Asserts nvidia-smi reports >= infrastructure.n_gpus_per_node GPUs. SKIPs when
# nvidia-smi is absent (the cases job may run on a CPU-only runner); a real
# /sft:run requires the GPUs. Does NOT judge whether they are free — that
# live check belongs to /sft:check, not a deterministic test.

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

N_GPUS="$(cfg runtime_info.input.infrastructure.n_gpus_per_node)"
[[ "$N_GPUS" =~ ^[0-9]+$ ]] || { echo "FAIL: infrastructure.n_gpus_per_node not an integer: $N_GPUS"; exit 1; }

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "SKIP: nvidia-smi not on PATH — cannot verify GPU count (CPU-only runner?)"
  exit 77
fi

HAVE="$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | wc -l | tr -d ' ')"
if [[ "${HAVE:-0}" -ge "$N_GPUS" ]]; then
  echo "INFO: nvidia-smi reports $HAVE GPU(s); config needs $N_GPUS"
  echo "PASS: enough GPUs visible"
else
  echo "FAIL: nvidia-smi reports $HAVE GPU(s) but config needs $N_GPUS"
  exit 1
fi
