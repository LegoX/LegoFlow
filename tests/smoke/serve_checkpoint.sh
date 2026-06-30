#!/usr/bin/env bash
# Root smoke helper: serve the sft checkpoint for the eval stage (MODE B).
#
# eval (stage 4) does not host a model — it drives Harbor containers that call
# an OpenAI-compatible endpoint through eval's OWN per-job LiteLLM proxy
# (start.sh, :4101). To evaluate the model sft (stage 3) just trained, we stand
# it up on the SAME remote GPU pod that trained it with vLLM ONLY, and point
# eval's llm_api.api_base_url straight at the raw vLLM /v1. This mirrors
# subblock/eval/scripts/serve_local_model.sh (MODE B), which evaluated this
# checkpoint end-to-end standalone.
#
#   eval host (CI runner)                       GPU pod (this script)
#   ┌──────────────────────────┐                ┌──────────────────────┐
#   │ Harbor agent containers  │                │ vLLM :8000/v1        │
#   │   -> eval LiteLLM :4101 ──┼───────────────▶│  (serves sft ckpt)   │
#   │      (start.sh)          │  api_base_url   └──────────────────────┘
#   └──────────────────────────┘  = raw vLLM
#
# NOTE: the OLD design also stood up a second LiteLLM on the pod and returned
# that wrapper URL; it failed because it looked for vLLM in artifacts/env/vllm
# (which the pod doesn't have — vLLM lives in the conda env vllm_0.18.1). MODE B
# drops the extra LiteLLM and uses the conda env.
#
# Usage:
#   bash tests/smoke/serve_checkpoint.sh start          # launch vLLM, print base URL
#   bash tests/smoke/serve_checkpoint.sh url            # just print the base URL it WOULD use
#   bash tests/smoke/serve_checkpoint.sh stop           # tear it down
#
# Inputs are read from the overlaid smoke configs (no second copy of the pod's
# address):
#   - remote host/key/port/dir  <- subblock/sft/config.yaml meta_info.resources
#   - checkpoint dir            <- sft training.output_dir (under artifacts/model/)
#   - vllm params               <- subblock/eval/config.yaml runtime_info.input.serving
#
# Exit 0 = served (and reachable), 77 = SKIP (no SSH / no checkpoint), 1 = FAIL.

set -uo pipefail

ACTION="${1:-start}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Read sft's pod resources + output_dir from the SMOKE config (that's what the
# checkpoint was trained with). subblock/sft/config.yaml is the production config
# at eval time — the eval stage overlays eval, not sft — so it would carry the
# wrong output_dir / resources here.
SFT_CFG="$ROOT_DIR/tests/smoke/sft/config.yaml"
EVAL_CFG="$ROOT_DIR/subblock/eval/config.yaml"

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

# --- Resolve the remote pod from the sft block's resources ------------------
R_IP="$(cfg "$SFT_CFG" meta_info.resources.ip)"
R_USER="$(cfg "$SFT_CFG" meta_info.resources.user)"
R_KEY="$(cfg "$SFT_CFG" meta_info.resources.key)"
R_PORT="$(cfg "$SFT_CFG" meta_info.resources.port)"
R_DIR="$(cfg "$SFT_CFG" meta_info.resources.directory)"
OUTPUT_DIR="$(cfg "$SFT_CFG" runtime_info.input.training.output_dir)"
CKPT_REMOTE="$R_DIR/subblock/sft/artifacts/model/$OUTPUT_DIR"

# --- Serving params from the eval config ------------------------------------
VLLM_PORT="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.port)";            VLLM_PORT="${VLLM_PORT:-8000}"
SERVED_NAME="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.served_model_name)"; SERVED_NAME="${SERVED_NAME:-root-smoke-sft}"
TP="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.tensor_parallel_size)";   TP="${TP:-1}"
MAXLEN="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.max_model_len)";      MAXLEN="${MAXLEN:-32768}"
GPUUTIL="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.gpu_memory_utilization)"; GPUUTIL="${GPUUTIL:-0.90}"
DTYPE="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.dtype)";               DTYPE="${DTYPE:-bfloat16}"
# vLLM's --api-key MUST equal the key eval's per-job LiteLLM forwards upstream
# (runtime_info.input.llm_api.api_key) or EVERY request 401s and vLLM returns
# empty choices -> all eval trials error -> eval FAIL. Derive it from the SAME
# config field eval reads so the two can never drift. (Root smoke 28371626594
# served 'dummy-key' while eval sent 'dummy-key-root-smoke' -> 199x HTTP 401.)
VLLM_API_KEY="$(cfg "$EVAL_CFG" runtime_info.input.llm_api.api_key)";           VLLM_API_KEY="${VLLM_API_KEY:-dummy-key}"

if [[ -z "$R_IP" || "$R_IP" == "local" || "$R_IP" == "null" ]]; then
  echo "SKIP: sft block has no remote pod (ip=$R_IP) — nowhere to serve the checkpoint"
  exit 77
fi

# vLLM lives in a conda env on the pod (the sft training env 'lf' lacks vllm).
VLLM_CONDA_ENV="${VLLM_CONDA_ENV:-vllm_0.18.1}"
CONDA_SH="${CONDA_SH:-/anaconda3/etc/profile.d/conda.sh}"

remote() {
  ssh -i "$R_KEY" -p "$R_PORT" \
      -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=20 \
      "$R_USER@$R_IP" "$@"
}

# MODE B: eval's own per-job LiteLLM (:4101) wraps the raw vLLM directly. The CI
# host reaches the pod over its INTERNAL IP — R_IP is the SSH/gateway address
# whose :VLLM_PORT is typically NOT exposed — so resolve the pod's internal IP
# (`hostname -I`) and point BASE_URL at the raw vLLM there.
POD_HOST="$(remote "hostname -I 2>/dev/null | awk '{print \$1}'" 2>/dev/null | tr -d '\r ')"
[[ -n "$POD_HOST" ]] || POD_HOST="$R_IP"
BASE_URL="http://${POD_HOST}:${VLLM_PORT}/v1"

case "$ACTION" in
  url)
    echo "$BASE_URL"
    exit 0
    ;;

  stop)
    echo "INFO: stopping vLLM on $R_IP"
    # Bracket trick so pkill -f doesn't match its own SSH command line.
    remote "pkill -f 'vllm [s]erve.*--port ${VLLM_PORT}' 2>/dev/null; \
            pkill -f 'api_[s]erver.*--port ${VLLM_PORT}' 2>/dev/null; true" || true
    echo "OK: serving torn down"
    exit 0
    ;;

  start)
    echo "INFO: serving sft checkpoint for eval (MODE B: vLLM-only; eval's LiteLLM wraps it)"
    echo "      pod        : $R_USER@$R_IP:$R_PORT"
    echo "      checkpoint : $CKPT_REMOTE"
    echo "      vLLM       : :$VLLM_PORT  tp=$TP  max_len=$MAXLEN  served_name=$SERVED_NAME  (conda $VLLM_CONDA_ENV)"
    echo "      base_url   : $BASE_URL   api_key=$VLLM_API_KEY (matches eval llm_api.api_key)"

    if ! remote "test -f '$CKPT_REMOTE/config.json'"; then
      echo "SKIP: no checkpoint at $CKPT_REMOTE on the pod (sft stage didn't persist a model)"
      exit 77
    fi

    LOG_DIR="$R_DIR/subblock/sft/artifacts/logs"
    # vLLM in the pod's conda env. Same flags as serve_local_model.sh (MODE B).
    remote "set -e; mkdir -p '$LOG_DIR';
      source '$CONDA_SH' 2>/dev/null && conda activate '$VLLM_CONDA_ENV' \
        || { echo 'NO_CONDA: cannot source $CONDA_SH / activate $VLLM_CONDA_ENV'; exit 3; };
      command -v vllm >/dev/null 2>&1 || { echo 'NO_VLLM: vllm not on PATH in $VLLM_CONDA_ENV'; exit 3; };
      # Guard on the LISTENING endpoint, not pgrep: a pgrep -f for 'vllm serve'
      # would self-match this very SSH command line (it contains the launch), so
      # it would always think vLLM is already up and never start it.
      if ! curl -fsS http://127.0.0.1:${VLLM_PORT}/health >/dev/null 2>&1; then
        nohup vllm serve '$CKPT_REMOTE' --host 0.0.0.0 --port ${VLLM_PORT} \
          --api-key '$VLLM_API_KEY' --served-model-name '$SERVED_NAME' \
          --tensor-parallel-size ${TP} --max-model-len ${MAXLEN} \
          --gpu-memory-utilization ${GPUUTIL} --dtype ${DTYPE} \
          --trust-remote-code --enable-auto-tool-choice --tool-call-parser hermes \
          > '$LOG_DIR/vllm-root-smoke.log' 2>&1 &
        echo \"vLLM launched pid=\$!\";
      else echo 'vLLM already healthy on :${VLLM_PORT}'; fi" || { echo "FAIL: could not launch vLLM (see above)"; exit 1; }

    # Wait for vLLM /health on the pod (model load is slow; allow ~15 min).
    echo "INFO: waiting for vLLM /health ..."
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

    # Confirm the raw vLLM is reachable from the CI host (where eval's Harbor
    # containers + per-job LiteLLM run).
    echo "INFO: confirming $BASE_URL reachable from CI host ..."
    ok=0
    for _ in $(seq 1 18); do
      if curl -fsS "${BASE_URL}/models" -H "Authorization: Bearer ${VLLM_API_KEY}" >/dev/null 2>&1; then ok=1; break; fi
      sleep 5
    done
    if [[ "$ok" != 1 ]]; then
      echo "FAIL: raw vLLM not reachable from CI host at $BASE_URL"
      echo "      (the pod's $VLLM_PORT may not be exposed to the runner network)"
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
