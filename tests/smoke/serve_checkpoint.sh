#!/usr/bin/env bash
# Root smoke helper: serve the sft checkpoint for the eval stage.
#
# eval (stage 4) does not host a model — it drives Harbor containers that call
# an OpenAI-compatible endpoint through a LiteLLM proxy. To evaluate the model
# sft (stage 3) just trained, we stand it up on the SAME remote GPU pod that
# trained it: vLLM serves the checkpoint, LiteLLM wraps it (so eval's existing
# hosted_vllm/ provider path works unchanged), and we print the wrapper's URL.
#
# Usage:
#   bash tests/smoke/serve_checkpoint.sh start          # launch vLLM + LiteLLM, print base URL
#   bash tests/smoke/serve_checkpoint.sh url            # just print the base URL it WOULD use
#   bash tests/smoke/serve_checkpoint.sh stop           # tear both down
#
# Inputs are read from the overlaid smoke configs (no second copy of the pod's
# address):
#   - remote host/key/port/dir  <- subblock/sft/config.yaml meta_info.resources
#   - checkpoint dir            <- sft training.output_dir (under artifacts/model/)
#   - vllm/litellm params       <- subblock/eval/config.yaml runtime_info.input.serving
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
TP="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.tensor_parallel_size)";   TP="${TP:-8}"
MAXLEN="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.max_model_len)";      MAXLEN="${MAXLEN:-32768}"
GPUUTIL="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.gpu_memory_utilization)"; GPUUTIL="${GPUUTIL:-0.90}"
DTYPE="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.dtype)";               DTYPE="${DTYPE:-bfloat16}"
LITELLM_PORT="$(cfg "$EVAL_CFG" runtime_info.input.serving.litellm.port)";      LITELLM_PORT="${LITELLM_PORT:-4110}"
LITELLM_KEY="$(cfg "$EVAL_CFG" runtime_info.input.serving.litellm.master_key)"; LITELLM_KEY="${LITELLM_KEY:-dummy-key-root-smoke}"

if [[ -z "$R_IP" || "$R_IP" == "local" || "$R_IP" == "null" ]]; then
  echo "SKIP: sft block has no remote pod (ip=$R_IP) — nowhere to serve the checkpoint"
  exit 77
fi

# The base URL eval's Harbor containers (on the CI host) call. The pod exposes
# $LITELLM_PORT; eval's hosted_vllm provider expects an OpenAI-compatible /v1.
BASE_URL="http://${R_IP}:${LITELLM_PORT}/v1"

remote() {
  ssh -i "$R_KEY" -p "$R_PORT" \
      -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=20 \
      "$R_USER@$R_IP" "$@"
}

case "$ACTION" in
  url)
    echo "$BASE_URL"
    exit 0
    ;;

  stop)
    echo "INFO: stopping vLLM + LiteLLM on $R_IP"
    remote "pkill -f 'vllm.entrypoints.openai.api_server.*--port ${VLLM_PORT}' 2>/dev/null; \
            pkill -f 'litellm.*--port ${LITELLM_PORT}' 2>/dev/null; true" || true
    echo "OK: serving torn down"
    exit 0
    ;;

  start)
    echo "INFO: serving sft checkpoint for eval"
    echo "      pod        : $R_USER@$R_IP:$R_PORT"
    echo "      checkpoint : $CKPT_REMOTE"
    echo "      vLLM       : :$VLLM_PORT  tp=$TP  max_len=$MAXLEN  served_name=$SERVED_NAME"
    echo "      LiteLLM    : :$LITELLM_PORT  -> base_url=$BASE_URL"

    if ! remote "test -f '$CKPT_REMOTE/config.json'"; then
      echo "SKIP: no checkpoint at $CKPT_REMOTE on the pod (sft stage didn't persist a model)"
      exit 77
    fi

    LOG_DIR="$R_DIR/subblock/sft/artifacts/logs"
    # 1) vLLM. Prefer the dedicated vLLM venv (artifacts/env/vllm) — the sft
    # training env (lf) does NOT ship vllm. Build it once with:
    #   uv venv artifacts/env/vllm --python 3.12 && uv pip install --python \
    #     artifacts/env/vllm/bin/python vllm
    remote "set -e; mkdir -p '$LOG_DIR'; cd '$R_DIR/subblock/sft';
      PYBIN=artifacts/env/vllm/bin/python; [ -x \"\$PYBIN\" ] || PYBIN=python3;
      if ! \"\$PYBIN\" -c 'import vllm' 2>/dev/null; then echo 'NO_VLLM: '\"\$PYBIN\"' lacks vllm — build artifacts/env/vllm'; exit 3; fi
      if ! pgrep -f 'api_server.*--port ${VLLM_PORT}' >/dev/null 2>&1; then
        nohup \"\$PYBIN\" -m vllm.entrypoints.openai.api_server \
          --model '$CKPT_REMOTE' --served-model-name '$SERVED_NAME' \
          --port ${VLLM_PORT} --tensor-parallel-size ${TP} \
          --max-model-len ${MAXLEN} --gpu-memory-utilization ${GPUUTIL} \
          --dtype ${DTYPE} --trust-remote-code \
          > '$LOG_DIR/vllm-root-smoke.log' 2>&1 &
        echo \"vLLM launched pid=\$!\";
      else echo 'vLLM already running'; fi" || { echo "FAIL: could not launch vLLM"; exit 1; }

    # 2) Wait for vLLM /health (up to ~10 min — model load on 8 GPUs is slow).
    echo "INFO: waiting for vLLM /health ..."
    ok=0
    for _ in $(seq 1 60); do
      if remote "curl -fsS http://127.0.0.1:${VLLM_PORT}/health >/dev/null 2>&1"; then ok=1; break; fi
      sleep 10
    done
    if [[ "$ok" != 1 ]]; then
      echo "FAIL: vLLM did not become healthy in time. Tail of vllm log:"
      remote "tail -30 '$LOG_DIR/vllm-root-smoke.log' 2>/dev/null" || true
      exit 1
    fi
    echo "INFO: vLLM healthy"

    # 3) LiteLLM wrapper in front of vLLM (so eval's hosted_vllm path is unchanged).
    remote "set -e; cd '$R_DIR/subblock/sft';
      LBIN=artifacts/env/litellm-venv/bin/litellm; [ -x \"\$LBIN\" ] || LBIN=litellm;
      cat > '$LOG_DIR/litellm-root-smoke.yaml' <<YAML
model_list:
  - model_name: ${SERVED_NAME}
    litellm_params:
      model: hosted_vllm/${SERVED_NAME}
      api_base: http://127.0.0.1:${VLLM_PORT}/v1
      api_key: dummy
litellm_settings:
  drop_params: true
YAML
      if ! pgrep -f 'litellm.*--port ${LITELLM_PORT}' >/dev/null 2>&1; then
        LITELLM_MASTER_KEY='${LITELLM_KEY}' nohup \"\$LBIN\" \
          --config '$LOG_DIR/litellm-root-smoke.yaml' --port ${LITELLM_PORT} --host 0.0.0.0 \
          > '$LOG_DIR/litellm-root-smoke.log' 2>&1 &
        echo \"LiteLLM launched pid=\$!\";
      else echo 'LiteLLM already running'; fi" || { echo "FAIL: could not launch LiteLLM"; exit 1; }

    # 4) Wait for LiteLLM /health/readiness from the CI host (must be reachable
    #    there — that's where eval's Harbor containers call from).
    echo "INFO: waiting for LiteLLM at $BASE_URL ..."
    ok=0
    for _ in $(seq 1 30); do
      if curl -fsS "http://${R_IP}:${LITELLM_PORT}/health/readiness" >/dev/null 2>&1 \
         || curl -fsS "${BASE_URL}/models" -H "Authorization: Bearer ${LITELLM_KEY}" >/dev/null 2>&1; then
        ok=1; break
      fi
      sleep 10
    done
    if [[ "$ok" != 1 ]]; then
      echo "FAIL: LiteLLM not reachable from the CI host at $BASE_URL"
      echo "      (the pod's $LITELLM_PORT may not be exposed to the runner network)"
      remote "tail -20 '$LOG_DIR/litellm-root-smoke.log' 2>/dev/null" || true
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
