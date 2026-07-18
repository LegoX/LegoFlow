#!/usr/bin/env bash
# Root smoke helper: serve the trainer checkpoint for the evaluator stage.
#
# evaluator (stage 4) does not host a model — it drives Harbor containers that call
# an OpenAI-compatible endpoint through evaluator's OWN per-job LiteLLM proxy
# (start.sh, :4101). To evaluate the model trainer (stage 3) just trained, we stand
# it up on the SAME remote GPU pod that trained it with vLLM, and point evaluator's
# llm_api.api_base_url at it.
#
#   evaluator host (CI runner)                        GPU pod (this script)
#   ┌──────────────────────────┐                 ┌──────────────────────┐
#   │ Harbor agent containers  │   ssh -L 8000   │ vLLM :8000/v1        │
#   │   -> evaluator LiteLLM :4101 ──┼───localhost────▶│  (DP across N GPUs)  │
#   │      (start.sh)          │  127.0.0.1:8000  └──────────────────────┘
#   └──────────────────────────┘
#
# Design notes (fixes from the first end-to-end root-smoke run):
#   * REACHABILITY via SSH port-forward. The pod's :VLLM_PORT is typically NOT
#     exposed to the runner network, and resolving the pod's internal IP is
#     fragile (that's what made the earlier serve "succeed then be unreachable").
#     Instead we open `ssh -L 127.0.0.1:PORT:127.0.0.1:PORT` from the runner and
#     hand evaluator a plain localhost URL — works regardless of pod network topology.
#   * DATA PARALLEL across all free GPUs. An 8B model fits on one GPU, so tensor
#     parallelism wastes the other 7. `--data-parallel-size N` runs N replicas
#     behind one endpoint (~Nx throughput). data_parallel_size defaults to the
#     number of FREE GPUs on the pod (never steals a busy GPU on the shared pod).
#   * CHECKPOINT AUTO-DETECT. Weights land in <output_dir>/checkpoint-*/ (LLaMA-
#     Factory save_only_model), not <output_dir> itself. We pick the newest
#     checkpoint-*/ that has a config.json, else fall back to <output_dir>.
#
# vLLM lives in a conda env on the pod (the trainer training env 'lf' lacks vllm).
#
# Usage:
#   bash tests/smoke/serve_checkpoint.sh start   # launch vLLM + tunnel, print base URL
#   bash tests/smoke/serve_checkpoint.sh url     # print the base URL it WOULD use
#   bash tests/smoke/serve_checkpoint.sh stop    # tear down vLLM + tunnel
#
# Exit 0 = served (and reachable), 77 = SKIP (no SSH / no checkpoint), 1 = FAIL.

set -uo pipefail

ACTION="${1:-start}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Read trainer's pod resources + output_dir from the SMOKE config (that's what the
# checkpoint was trained with). subblock/trainer/config.yaml is the production config
# at evaluator time — the evaluator stage overlays evaluator, not trainer — so it would carry the
# wrong output_dir / resources here.
SFT_CFG="$ROOT_DIR/tests/smoke/trainer/config.yaml"
EVAL_CFG="$ROOT_DIR/subblock/evaluator/config.yaml"
TUNNEL_PIDFILE="$ROOT_DIR/.smoke-run/serve-tunnel.pid"

cfg() {  # cfg <file> <dotted-key>
  python3 - "$1" "$2" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

# --- Resolve the remote pod from the trainer block's resources ------------------
R_IP="$(cfg "$SFT_CFG" meta_info.resources.ip)"
R_USER="$(cfg "$SFT_CFG" meta_info.resources.user)"
R_KEY="$(cfg "$SFT_CFG" meta_info.resources.key)"
R_PORT="$(cfg "$SFT_CFG" meta_info.resources.port)"
R_DIR="$(cfg "$SFT_CFG" meta_info.resources.directory)"
OUTPUT_DIR="$(cfg "$SFT_CFG" runtime_info.input.training.output_dir)"
MODEL_ROOT="$R_DIR/subblock/trainer/artifacts/model/$OUTPUT_DIR"

# --- Serving params from the evaluator config ------------------------------------
VLLM_PORT="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.port)";            VLLM_PORT="${VLLM_PORT:-8000}"
SERVED_NAME="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.served_model_name)"; SERVED_NAME="${SERVED_NAME:-root-smoke-sft}"
TP="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.tensor_parallel_size)";   TP="${TP:-1}"
DP="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.data_parallel_size)"      # blank/"auto" => all free GPUs
MAXLEN="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.max_model_len)";      MAXLEN="${MAXLEN:-32768}"
GPUUTIL="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.gpu_memory_utilization)"; GPUUTIL="${GPUUTIL:-0.90}"
DTYPE="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.dtype)";               DTYPE="${DTYPE:-bfloat16}"
GPU_FREE_USED_MIB="${GPU_FREE_USED_MIB:-4000}"
# vLLM's --api-key MUST equal the key evaluator's per-job LiteLLM forwards upstream
# (runtime_info.input.llm_api.api_key) or EVERY request 401s and vLLM returns
# empty choices -> all evaluator trials error -> evaluator FAIL. Derive it from the SAME
# config field evaluator reads so the two can never drift.
VLLM_API_KEY="$(cfg "$EVAL_CFG" runtime_info.input.llm_api.api_key)";           VLLM_API_KEY="${VLLM_API_KEY:-dummy-key}"

BASE_URL="http://127.0.0.1:${VLLM_PORT}/v1"   # always localhost via the SSH tunnel

if [[ -z "$R_IP" || "$R_IP" == "local" || "$R_IP" == "null" ]]; then
  echo "SKIP: trainer block has no remote pod (ip=$R_IP) — nowhere to serve the checkpoint"
  exit 77
fi

VLLM_CONDA_ENV="${VLLM_CONDA_ENV:-vllm_0.18.1}"
CONDA_SH="${CONDA_SH:-/anaconda3/etc/profile.d/conda.sh}"

remote() {
  ssh -i "$R_KEY" -p "$R_PORT" \
      -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=20 \
      "$R_USER@$R_IP" "$@"
}

open_tunnel() {  # localhost:VLLM_PORT -> pod 127.0.0.1:VLLM_PORT
  mkdir -p "$(dirname "$TUNNEL_PIDFILE")"
  # already up?
  if curl -fsS -m 5 "${BASE_URL}/models" -H "Authorization: Bearer ${VLLM_API_KEY}" >/dev/null 2>&1; then
    return 0
  fi
  ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
      -i "$R_KEY" -p "$R_PORT" \
      -L "127.0.0.1:${VLLM_PORT}:127.0.0.1:${VLLM_PORT}" \
      "$R_USER@$R_IP" >/dev/null 2>&1 &
  echo $! > "$TUNNEL_PIDFILE"
}

close_tunnel() {
  [[ -f "$TUNNEL_PIDFILE" ]] || return 0
  local pid; pid="$(cat "$TUNNEL_PIDFILE" 2>/dev/null)"
  [[ -n "$pid" ]] && kill "$pid" 2>/dev/null
  rm -f "$TUNNEL_PIDFILE"
}

# Resolve the servable checkpoint dir on the pod: newest checkpoint-*/ with a
# config.json, else the output_dir itself. Prints the absolute pod path or "".
resolve_remote_ckpt() {
  remote "bash -s" <<REMOTE_EOF 2>/dev/null | tr -d '\r'
root='$MODEL_ROOT'
ck="\$(ls -d "\$root"/checkpoint-* 2>/dev/null | sort -t- -k2 -n | tail -1)"
if [ -n "\$ck" ] && [ -f "\$ck/config.json" ]; then echo "\$ck";
elif [ -f "\$root/config.json" ]; then echo "\$root";
else echo ""; fi
REMOTE_EOF
}

case "$ACTION" in
  url)
    echo "$BASE_URL"
    exit 0
    ;;

  stop)
    echo "INFO: stopping vLLM on $R_IP + local tunnel"
    remote "pkill -f 'vllm [s]erve.*--port ${VLLM_PORT}' 2>/dev/null; \
            pkill -f 'api_[s]erver.*--port ${VLLM_PORT}' 2>/dev/null; \
            tmux kill-session -t smoke-vllm 2>/dev/null; true" || true
    close_tunnel
    echo "OK: serving torn down"
    exit 0
    ;;

  start)
    CKPT_REMOTE="$(resolve_remote_ckpt)"
    if [[ -z "$CKPT_REMOTE" ]]; then
      echo "SKIP: no checkpoint (config.json) under $MODEL_ROOT on the pod (trainer stage didn't persist a model)"
      exit 77
    fi

    # data_parallel_size: explicit config, else count FREE GPUs (shared pod —
    # never claim a GPU someone else is using).
    if [[ -z "$DP" || "$DP" == "auto" || "$DP" == "null" ]]; then
      FREE="$(remote "nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits" 2>/dev/null \
                | tr -d '\r' | awk -v t="$GPU_FREE_USED_MIB" '$1<t{n++} END{print n+0}')"
      [[ "$FREE" =~ ^[0-9]+$ ]] || FREE=1
      DP=$(( FREE / TP ))
      (( DP < 1 )) && DP=1
    fi

    echo "INFO: serving trainer checkpoint for evaluator (vLLM DP=$DP TP=$TP; evaluator's LiteLLM wraps it)"
    echo "      pod        : $R_USER@$R_IP:$R_PORT"
    echo "      checkpoint : $CKPT_REMOTE"
    echo "      vLLM       : :$VLLM_PORT  dp=$DP tp=$TP  max_len=$MAXLEN  served_name=$SERVED_NAME  (conda $VLLM_CONDA_ENV)"
    echo "      base_url   : $BASE_URL   api_key=$VLLM_API_KEY (matches evaluator llm_api.api_key)"

    LOG_DIR="$R_DIR/subblock/trainer/artifacts/logs"
    DP_FLAG=""; [[ "$DP" -gt 1 ]] && DP_FLAG="--data-parallel-size ${DP}"
    # vLLM in the pod's conda env, launched inside a detached tmux session so it
    # survives the SSH connection closing (a bare nohup child can get reaped).
    remote "bash -s" <<REMOTE_EOF || { echo "FAIL: could not launch vLLM (see above)"; exit 1; }
set -e
mkdir -p '$LOG_DIR'
source '$CONDA_SH' 2>/dev/null && conda activate '$VLLM_CONDA_ENV' \
  || { echo 'NO_CONDA: cannot source $CONDA_SH / activate $VLLM_CONDA_ENV'; exit 3; }
command -v vllm >/dev/null 2>&1 || { echo 'NO_VLLM: vllm not on PATH in $VLLM_CONDA_ENV'; exit 3; }
if curl -fsS http://127.0.0.1:${VLLM_PORT}/health >/dev/null 2>&1; then
  # Only REUSE the running server if it is serving the checkpoint we intend to
  # evaluate. A prior smoke (cancelled after serving, or --keep-serving) may hold
  # the port with an OLDER checkpoint; reusing it would silently score the wrong
  # model. If the running vllm cmdline does not reference this checkpoint, kill it
  # and relaunch for '$CKPT_REMOTE'.
  if pgrep -af 'vllm [s]erve' 2>/dev/null | grep -qF -- '$CKPT_REMOTE'; then
    echo 'vLLM already healthy on :${VLLM_PORT} serving the current checkpoint'; exit 0
  fi
  echo 'vLLM on :${VLLM_PORT} serves a different/older checkpoint — restarting for $CKPT_REMOTE'
  pkill -f 'vllm [s]erve.*--port ${VLLM_PORT}' 2>/dev/null || true
  pkill -f 'api_[s]erver.*--port ${VLLM_PORT}' 2>/dev/null || true
  sleep 3
fi
tmux kill-session -t smoke-vllm 2>/dev/null || true
tmux new-session -d -s smoke-vllm "vllm serve '$CKPT_REMOTE' --host 0.0.0.0 --port ${VLLM_PORT} \
    --api-key '$VLLM_API_KEY' --served-model-name '$SERVED_NAME' \
    ${DP_FLAG} --tensor-parallel-size ${TP} --max-model-len ${MAXLEN} \
    --gpu-memory-utilization ${GPUUTIL} --dtype ${DTYPE} \
    --trust-remote-code --enable-auto-tool-choice --tool-call-parser hermes \
    > '$LOG_DIR/vllm-root-smoke.log' 2>&1"
echo 'vLLM launched in tmux session smoke-vllm'
REMOTE_EOF

    # Wait for vLLM /health on the pod (model load + DP replicas + CUDA graph
    # capture is slow; allow ~15 min).
    echo "INFO: waiting for vLLM /health on the pod ..."
    ok=0
    for _ in $(seq 1 90); do
      if remote "curl -fsS http://127.0.0.1:${VLLM_PORT}/health >/dev/null 2>&1"; then ok=1; break; fi
      sleep 10
    done
    if [[ "$ok" != 1 ]]; then
      echo "FAIL: vLLM did not become healthy in time. Tail of vllm log:"
      remote "tail -30 '$LOG_DIR/vllm-root-smoke.log' 2>/dev/null" || true
      exit 1
    fi
    echo "INFO: vLLM healthy on pod"

    # Open the SSH port-forward and confirm reachability from the runner.
    echo "INFO: opening SSH tunnel 127.0.0.1:${VLLM_PORT} -> pod ..."
    open_tunnel
    ok=0
    for _ in $(seq 1 24); do
      if curl -fsS "${BASE_URL}/models" -H "Authorization: Bearer ${VLLM_API_KEY}" >/dev/null 2>&1; then ok=1; break; fi
      sleep 5
    done
    if [[ "$ok" != 1 ]]; then
      echo "FAIL: vLLM not reachable via tunnel at $BASE_URL (ssh -L failed?)"
      close_tunnel
      exit 1
    fi
    echo "PASS: serving up — $BASE_URL"
    echo "$BASE_URL"
    exit 0
    ;;

  *)
    echo "usage: $0 {start|url|stop}" >&2
    exit 2
    ;;
esac
