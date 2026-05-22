#!/bin/bash
# Health-check: verify configs and paths without launching training.
set -e

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$BLOCK_DIR/repos/harbor-verl-train"
CONFIG="$BLOCK_DIR/inputs.yaml"

cfg() { python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG')); print(c$1)"; }
abspath() {
    local p="$1"
    if [[ "$p" = /* ]]; then echo "$p"; else echo "$BLOCK_DIR/$p"; fi
}

echo "[rl-train/dryrun] Checking repo internal configs..."
python3 -c "import yaml; yaml.safe_load(open('$REPO/workspace/config.yaml'))" && echo "  workspace/config.yaml OK"

echo "[rl-train/dryrun] Checking input paths from inputs/index.yaml..."
PATHS=(
  "$(abspath "$(cfg "['model']['model_path']")")"
  "$(abspath "$(cfg "['infrastructure']['k8s_kubeconfig']")")"
  "$(abspath "$(cfg "['data']['swe_parquet']")")"
  "$(abspath "$(cfg "['data']['val_parquet']")")"
  "$(abspath "$(cfg "['data']['tasks_dir']")")"
  "$(abspath "$(cfg "['data']['val_tasks_dir']")")"
  "$REPO/examples/harbor/config/harbor_online_cc.yaml"
)
for p in "${PATHS[@]}"; do
  if [ -e "$p" ]; then echo "  OK       $p"; else echo "  MISSING  $p"; fi
done

echo "[rl-train/dryrun] Checking GPU availability..."
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "  nvidia-smi not available"

echo "[rl-train/dryrun] Done."
