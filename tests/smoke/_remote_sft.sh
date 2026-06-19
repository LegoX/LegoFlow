#!/usr/bin/env bash
# Internal helper for run_pipeline.sh stage 3 when sft runs on a remote GPU pod.
#
# Stages the merged combined-LF dataset + the lowered smoke config onto the pod,
# launches scripts/start.sh there, polls the pod for train_results.json, and
# fetches it (plus trainer_state.json) back into the LOCAL sft block so the
# local verify.sh sees the same artifact at the same path. The persisted
# checkpoint stays on the pod — serve_checkpoint.sh (eval stage) serves it there.
#
#   bash tests/smoke/_remote_sft.sh <sft_block_dir> <merged_rel> <budget> <dry_run>
#
# Reads the pod address from <sft_block_dir>/config.yaml meta_info.resources.

set -uo pipefail
FB="${1:?sft block dir}"; MERGED_REL="${2:?merged rel path}"; BUDGET="${3:-3000}"; DRY="${4:-0}"
CFG="$FB/config.yaml"

cfg() { python3 - "$CFG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

R_IP="$(cfg meta_info.resources.ip)"; R_USER="$(cfg meta_info.resources.user)"
R_KEY="$(cfg meta_info.resources.key)"; R_PORT="$(cfg meta_info.resources.port)"
R_DIR="$(cfg meta_info.resources.directory)"; OUT="$(cfg runtime_info.input.training.output_dir)"
REMOTE_SFT="$R_DIR/subblock/sft"

SSH() { ssh -i "$R_KEY" -p "$R_PORT" -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=20 "$R_USER@$R_IP" "$@"; }
SCP_TO() { scp -i "$R_KEY" -P "$R_PORT" -o StrictHostKeyChecking=accept-new -o BatchMode=yes "$1" "$R_USER@$R_IP:$2"; }
SCP_FROM() { scp -i "$R_KEY" -P "$R_PORT" -o StrictHostKeyChecking=accept-new -o BatchMode=yes "$R_USER@$R_IP:$1" "$2"; }

if [[ "$DRY" == 1 ]]; then
  echo "[DRY-RUN] scp $MERGED_REL + config.yaml -> $R_USER@$R_IP:$REMOTE_SFT ; ssh start.sh ; poll ; fetch train_results.json"
  exit 0
fi

# Probe SSH first — SKIP cleanly if the pod is unreachable.
if ! SSH 'echo ok' >/dev/null 2>&1; then
  echo "SKIP: sft pod $R_IP unreachable over SSH — cannot run remote training"
  exit 77
fi

echo "INFO: staging data + config onto $R_USER@$R_IP:$REMOTE_SFT"
SSH "mkdir -p '$REMOTE_SFT/$(dirname "$MERGED_REL")' '$REMOTE_SFT/artifacts/logs' '$REMOTE_SFT/artifacts/model'"
SCP_TO "$FB/$MERGED_REL" "$REMOTE_SFT/$MERGED_REL"
SCP_TO "$CFG" "$REMOTE_SFT/config.yaml"
# Drop any stale run dir so verify isn't fooled by a previous run.
SSH "rm -rf '$REMOTE_SFT/artifacts/model/$OUT'"

echo "INFO: launching remote start.sh (budget ${BUDGET}s)"
SSH "set -e; cd '$REMOTE_SFT'; PATH=/root/.local/bin:\$PATH nohup bash scripts/start.sh > artifacts/logs/root-smoke-sft.log 2>&1 & disown; echo REMOTE_PID=\$!" \
  || { echo "FAIL: could not launch remote start.sh"; exit 1; }

echo "INFO: polling pod for train_results.json"
START=$(date +%s); DEADLINE=$((START + BUDGET))
while (( $(date +%s) < DEADLINE )); do
  if SSH "test -f '$REMOTE_SFT/artifacts/model/$OUT/train_results.json'" 2>/dev/null; then
    echo "INFO: remote train_results.json present after $(( $(date +%s) - START ))s — fetching"
    mkdir -p "$FB/artifacts/model/$OUT"
    SCP_FROM "$REMOTE_SFT/artifacts/model/$OUT/train_results.json" "$FB/artifacts/model/$OUT/train_results.json" || true
    SCP_FROM "$REMOTE_SFT/artifacts/model/$OUT/trainer_state.json" "$FB/artifacts/model/$OUT/trainer_state.json" || true
    exit 0
  fi
  sleep 30
done
echo "WARN: budget exhausted; train_results.json not present on pod. Tail of remote log:"
SSH "tail -25 '$REMOTE_SFT/artifacts/logs/root-smoke-sft.log' 2>/dev/null" || true
exit 0
