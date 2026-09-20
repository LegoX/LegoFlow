#!/usr/bin/env bash
# Serve a LOCAL model checkpoint as an OpenAI-compatible endpoint via vLLM, so
# the eval block can benchmark a custom (e.g. SFT/RL) model instead of a remote
# upstream API.
#
# WHERE THIS RUNS:
#   On a GPU node (vLLM needs GPUs). The eval block's node (config.yaml ->
#   meta_info.resources.ip) is CPU-only, so DO NOT run this there. Start vLLM on
#   a GPU host, then point the eval block's llm_api.api_base_url at this host's
#   IP:PORT.
#
# WHY vLLM-ONLY (no LiteLLM here):
#   eval/scripts/start.sh already launches its own per-job LiteLLM proxy that
#   wraps llm_api.api_base_url, adds the Harbor trajectory_logger callback, and
#   serves the Anthropic-format endpoint the agents use. Starting a second
#   LiteLLM here would collide on the proxy port and bypass that logging. So
#   this script serves ONLY the raw vLLM OpenAI endpoint; eval's LiteLLM sits in
#   front of it.
#
#   eval node                              GPU node (this script)
#   ┌───────────────────────────┐         ┌──────────────────────────┐
#   │ agent container           │         │ vLLM  :8000/v1           │
#   │   -> eval LiteLLM :4101 ───┼────────▶│   (serves local ckpt)    │
#   │      (start.sh)           │ api_base │                          │
#   └───────────────────────────┘ _url     └──────────────────────────┘
#
# USAGE:
#   bash scripts/serve_local_model.sh
#   MODEL_PATH=/path/to/ckpt MODEL_NAME=my-model bash scripts/serve_local_model.sh
#
# The defaults are tuned for the active Qwen3.5 checkpoint. Parser/model-specific
# settings are env-overridable; for another architecture, review
# TOOL_CALL_PARSER, LANGUAGE_MODEL_ONLY, GDN_PREFILL_BACKEND, and DTYPE. After
# it reports "ready", copy the printed llm_api block into config.yaml and run the
# eval preflight on the eval node.
set -euo pipefail

# ------------------------------------------------------------------------------
# Model settings
#
# The checkpoint path and served name are CONFIGURATION, not script constants:
# they come from config.yaml -> runtime_info.input.local_model_serving. Env vars
# MODEL_PATH / MODEL_NAME still override for one-off runs.
# ------------------------------------------------------------------------------
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${EVAL_CONFIG:-$BLOCK_DIR/config.yaml}"

cfg() {
  python3 - "$CONFIG" "$1" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required", file=sys.stderr); sys.exit(2)
config_path, dotted_key = sys.argv[1], sys.argv[2]
with open(config_path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}
value = data
for part in dotted_key.split("."):
    if not isinstance(value, dict):
        value = None; break
    value = value.get(part)
print("" if value is None else value)
PY
}

if [[ -z "${MODEL_PATH:-}" ]]; then
  MODEL_PATH="$(cfg runtime_info.input.local_model_serving.model_path)"
  if [[ -n "$MODEL_PATH" && "$MODEL_PATH" != /* ]]; then
    MODEL_PATH="$BLOCK_DIR/$MODEL_PATH"
  fi
fi
MODEL_NAME="${MODEL_NAME:-$(cfg runtime_info.input.local_model_serving.model_name)}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER-qwen3_coder}"
REASONING_PARSER="${REASONING_PARSER:-}"

[[ -n "$MODEL_PATH" ]] || { echo "ERROR: model path not set. Add it to config.yaml -> runtime_info.input.local_model_serving.model_path, or pass MODEL_PATH=..." >&2; exit 1; }
[[ -n "$MODEL_NAME" ]] || { echo "ERROR: model name not set. Add it to config.yaml -> runtime_info.input.local_model_serving.model_name, or pass MODEL_NAME=..." >&2; exit 1; }

# ------------------------------------------------------------------------------
# Conda env that has vLLM installed (created ONCE on the GPU node, not here).
#
# For this bf16 Qwen3.5-35B-A3B checkpoint, a plain pip install is
# enough — no source build:
#   conda create -y -n vllm_0.18.1 python=3.12
#   conda activate vllm_0.18.1
#   pip install vllm==0.18.1
#
# Only FP8 models with custom kernels (e.g. GLM-5.1-FP8) need the heavy
# source build in repos/harbor/scripts/serve_llm/install_vllm_32b717_cu128.sh.
#
# Set VLLM_CONDA_ENV="" to skip conda activation if vllm is already on PATH.
# ------------------------------------------------------------------------------
VLLM_CONDA_ENV="${VLLM_CONDA_ENV-vllm_0.18.1}"
# Auto-detect conda.sh from the user's home or a conventional /opt install and
# fall back to `conda info --base`. Override with
# CONDA_SH=... if your install lives elsewhere.
if [[ -z "${CONDA_SH:-}" ]]; then
  for _candidate in \
    "$HOME/miniconda3/etc/profile.d/conda.sh" \
    "$HOME/anaconda3/etc/profile.d/conda.sh" \
    "/opt/conda/etc/profile.d/conda.sh" \
    "/opt/anaconda3/etc/profile.d/conda.sh"; do
    [[ -f "$_candidate" ]] && { CONDA_SH="$_candidate"; break; }
  done
  if [[ -z "${CONDA_SH:-}" ]] && command -v conda >/dev/null 2>&1; then
    _conda_base="$(conda info --base 2>/dev/null || true)"
    [[ -n "$_conda_base" && -f "$_conda_base/etc/profile.d/conda.sh" ]] && CONDA_SH="$_conda_base/etc/profile.d/conda.sh"
  fi
fi

# ------------------------------------------------------------------------------
# Server settings.  HOST=0.0.0.0 so the eval node's LiteLLM can reach this.
# ------------------------------------------------------------------------------
HOST="${HOST:-0.0.0.0}"
VLLM_PORT="${VLLM_PORT:-8000}"
API_KEY="${API_KEY:-dummy-key}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"

# ------------------------------------------------------------------------------
# vLLM settings
# ------------------------------------------------------------------------------
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-$(cfg runtime_info.input.local_model_serving.tensor_parallel_size)}"
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-8}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-262144}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-32}"
VLLM_TIMEOUT="${VLLM_TIMEOUT:-600}"
LANGUAGE_MODEL_ONLY="${LANGUAGE_MODEL_ONLY:-1}"
GDN_PREFILL_BACKEND="${GDN_PREFILL_BACKEND-triton}"
DTYPE="${DTYPE:-bfloat16}"

[[ -d "$MODEL_PATH" ]] || { echo "ERROR: MODEL_PATH does not exist: $MODEL_PATH" >&2; exit 1; }

if [[ -n "$VLLM_CONDA_ENV" ]]; then
  [[ -f "$CONDA_SH" ]] || { echo "ERROR: conda profile not found: $CONDA_SH (set CONDA_SH= or VLLM_CONDA_ENV='')" >&2; exit 1; }
  # shellcheck disable=SC1090
  source "$CONDA_SH"
  conda activate "$VLLM_CONDA_ENV"
fi
if ! command -v vllm >/dev/null 2>&1; then
  cat >&2 <<EOF
ERROR: 'vllm' not on PATH (conda env: ${VLLM_CONDA_ENV:-<none>}).
Create it once on this GPU node, then re-run:
  conda create -y -n ${VLLM_CONDA_ENV:-vllm_0.18.1} python=3.12
  conda activate ${VLLM_CONDA_ENV:-vllm_0.18.1}
  pip install vllm==0.18.1
(FP8 models needing custom kernels: see repos/harbor/scripts/serve_llm/install_vllm_32b717_cu128.sh instead.)
EOF
  exit 1
fi

# ------------------------------------------------------------------------------
# Refuse to replace an existing listener on the target port. On shared GPU
# nodes, automatically killing an arbitrary process can interrupt another job.
# ------------------------------------------------------------------------------
# Match the Local Address:Port column ($4) ending in exactly :PORT, so :8000
# does not also match :18000 / :28000.
existing_listener="$(ss -tlnp 2>/dev/null | awk -v p=":${VLLM_PORT}\$" '$4 ~ p {print; exit}' || true)"
if [[ -n "$existing_listener" ]]; then
  echo "ERROR: port ${VLLM_PORT} is already in use; refusing to kill the existing listener." >&2
  echo "       ${existing_listener}" >&2
  echo "       Stop the owning service explicitly or choose another VLLM_PORT." >&2
  exit 1
fi

cleanup() {
  if [[ "${CLEANED_UP:-0}" == "1" ]]; then
    return
  fi
  CLEANED_UP=1
  if [[ -n "${VLLM_PID:-}" ]]; then
    echo ""
    echo "Stopping vLLM..."
    kill "${VLLM_PID}" 2>/dev/null || true
    wait "${VLLM_PID}" 2>/dev/null || true
    echo "Stopped."
  fi
}
# Include EXIT so the half-started vLLM child is reaped even when the script
# aborts via one of the set -e error paths below (not just on INT/TERM).
trap 'exit 130' INT
trap 'exit 143' TERM
trap cleanup EXIT

HOST_IP="${ADVERTISE_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
[[ -n "$HOST_IP" ]] || { echo "ERROR: could not determine advertised GPU-node IP; set ADVERTISE_HOST" >&2; exit 1; }

echo "=========================================="
echo "Starting vLLM for ${MODEL_NAME}"
echo "=========================================="
echo "Model path:   ${MODEL_PATH}"
echo "Served name:  ${MODEL_NAME}"
echo "Endpoint:     http://${HOST_IP}:${VLLM_PORT}/v1   (OpenAI format)"
echo "TP size:      ${TENSOR_PARALLEL_SIZE}    max-model-len: ${MAX_MODEL_LEN}"
echo "=========================================="
echo ""

VLLM_EXTRA_ARGS=()
if [[ -n "${REASONING_PARSER}" ]]; then
  VLLM_EXTRA_ARGS+=(--reasoning-parser "${REASONING_PARSER}")
fi
if [[ -n "${TOOL_CALL_PARSER}" ]]; then
  VLLM_EXTRA_ARGS+=(--enable-auto-tool-choice --tool-call-parser "${TOOL_CALL_PARSER}")
fi
if [[ "${LANGUAGE_MODEL_ONLY}" == "1" ]]; then
  VLLM_EXTRA_ARGS+=(--language-model-only)
fi
if [[ -n "${GDN_PREFILL_BACKEND}" ]]; then
  VLLM_EXTRA_ARGS+=(--gdn-prefill-backend "${GDN_PREFILL_BACKEND}")
fi

vllm serve "${MODEL_PATH}" \
    --host "${HOST}" \
    --port "${VLLM_PORT}" \
    --api-key "${API_KEY}" \
    --served-model-name "${MODEL_NAME}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --max-model-len "${MAX_MODEL_LEN}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --trust-remote-code \
    --enable-prefix-caching \
    --enable-chunked-prefill \
    --max-num-seqs "${MAX_NUM_SEQS}" \
    "${VLLM_EXTRA_ARGS[@]}" \
    --dtype "${DTYPE}" &
VLLM_PID=$!

echo "Waiting for vLLM on port ${VLLM_PORT} (timeout: ${VLLM_TIMEOUT}s)..."
for i in $(seq 1 "${VLLM_TIMEOUT}"); do
  if curl -s --connect-timeout 2 "http://localhost:${VLLM_PORT}/health" >/dev/null 2>&1; then
    echo "vLLM is ready! (took ~${i}s)"
    break
  fi
  kill -0 "${VLLM_PID}" 2>/dev/null || { echo "ERROR: vLLM process died during startup." >&2; exit 1; }
  sleep 1
done
curl -s --connect-timeout 2 "http://localhost:${VLLM_PORT}/health" >/dev/null 2>&1 \
  || { echo "ERROR: vLLM did not become ready within ${VLLM_TIMEOUT}s." >&2; exit 1; }

MODELS="$(curl -s --connect-timeout 5 -H "Authorization: Bearer ${API_KEY}" "http://localhost:${VLLM_PORT}/v1/models" || true)"
if echo "${MODELS}" | grep -Fq "${MODEL_NAME}"; then
  echo "[PASS] /v1/models returns ${MODEL_NAME}"
else
  echo "[WARN] /v1/models did not list ${MODEL_NAME}; response: ${MODELS}"
fi

cat <<EOF

==========================================================================
vLLM is serving ${MODEL_NAME}. Now point the eval block at it:

  config.yaml -> runtime_info.input.llm_api:
    api_key: "${API_KEY}"
    api_base_url: "http://${HOST_IP}:${VLLM_PORT}/v1"
    model: "openai/${MODEL_NAME}"
    input_cost_per_token: 0.0
    output_cost_per_token: 0.0

Then on the eval node:
  bash scripts/dryrun.sh
  bash scripts/probe_llm_completion.sh
  bash scripts/start.sh
Keep this process running (use tmux) for the whole eval job.
==========================================================================

EOF

wait
