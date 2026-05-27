#!/bin/bash
# Remove transient state from a previous run.
#
# Always preserved:  repos/harbor-verl-train/checkpoints/, wandb runs.
# Always cleared:    Ray temp, LiteLLM temp config + log + symlink.
# Optional:          --logs    also clear repos/harbor-verl-train/logs/*.log
#                    --trials  also clear repos/harbor-verl-train/harbor_trials/
#                    --pods    also delete k8s pods labeled harbor-run=<pod_name_prefix>
set -e

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$BLOCK_DIR/repos/harbor-verl-train"
CONFIG="$BLOCK_DIR/config.yaml"

CLEAR_LOGS=false
CLEAR_TRIALS=false
CLEAR_PODS=false
for arg in "$@"; do
    case "$arg" in
        --logs)   CLEAR_LOGS=true ;;
        --trials) CLEAR_TRIALS=true ;;
        --pods)   CLEAR_PODS=true ;;
        --all)    CLEAR_LOGS=true; CLEAR_TRIALS=true; CLEAR_PODS=true ;;
        *)        echo "Unknown flag: $arg"; exit 1 ;;
    esac
done

echo "[rl/clean] killing residual LiteLLM proxy on port 8002"
if [[ -f /tmp/litellm_cc_8002.pid ]]; then
    kill "$(cat /tmp/litellm_cc_8002.pid)" 2>/dev/null || true
fi
# Kill anything holding port 8002 (including orphaned uvicorn spawn workers)
python3 -c "
import os, glob, signal
with open('/proc/net/tcp') as f:
    for line in f.readlines()[1:]:
        parts = line.split()
        port = int(parts[1].split(':')[1], 16)
        if port == 8002:
            inode = parts[9]
            for fd in glob.glob('/proc/[0-9]*/fd/*'):
                try:
                    if f'socket:[{inode}]' in os.readlink(fd):
                        pid = int(fd.split('/')[2])
                        os.kill(pid, signal.SIGKILL)
                        print(f'  killed pid {pid}')
                except: pass
" 2>/dev/null || true

echo "[rl/clean] killing residual vLLM GPU workers"
pgrep -f 'vllm_server|vLLMHttpServer|VLLM::Worker' 2>/dev/null | xargs -r kill -9 2>/dev/null || true
nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | xargs -r kill -9 2>/dev/null || true

echo "[rl/clean] ray stop --force"
ray stop --force 2>/dev/null || true

echo "[rl/clean] removing /tmp/ray"
rm -rf /tmp/ray/* 2>/dev/null || true

echo "[rl/clean] removing /tmp/litellm_cc_* and /tmp/trajectory_logger.py symlink"
rm -f /tmp/litellm_cc_*.yaml /tmp/litellm_cc_*.log /tmp/litellm_cc_*.pid /tmp/trajectory_logger.py 2>/dev/null || true

if $CLEAR_LOGS; then
    echo "[rl/clean] --logs: clearing $REPO/logs/*.log"
    rm -f "$REPO/logs"/*.log 2>/dev/null || true
fi

if $CLEAR_TRIALS; then
    echo "[rl/clean] --trials: clearing $REPO/harbor_trials/"
    rm -rf "$REPO/harbor_trials" 2>/dev/null || true
fi

if $CLEAR_PODS; then
    PREFIX=$(python3 - "$CONFIG" <<'PY'
import sys, yaml
v = yaml.safe_load(open(sys.argv[1]))
print(v.get("runtime_info", {}).get("input", {}).get("harbor_agent", {}).get("pod_name_prefix", "") or "")
PY
    )
    KUBECONFIG_PATH=$(python3 - "$CONFIG" <<'PY'
import sys, yaml
v = yaml.safe_load(open(sys.argv[1]))
print(v.get("runtime_info", {}).get("input", {}).get("k8s", {}).get("kubeconfig", "") or "")
PY
    )
    if [[ -z "$PREFIX" ]]; then
        echo "[rl/clean] --pods: harbor_agent.pod_name_prefix is empty — skipping (would delete unrelated pods)"
    elif ! command -v kubectl >/dev/null; then
        echo "[rl/clean] --pods: kubectl not found — skipping"
    else
        echo "[rl/clean] --pods: deleting k8s pods with label harbor-run=$PREFIX"
        KUBECONFIG="$KUBECONFIG_PATH" kubectl delete pods -l "harbor-run=$PREFIX" --ignore-not-found 2>/dev/null || true
    fi
fi

echo "[rl/clean] Done. Checkpoints + wandb runs preserved."
