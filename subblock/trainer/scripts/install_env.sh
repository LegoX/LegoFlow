#!/usr/bin/env bash
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"
CONFIG_PYTHON="${CONFIG_PYTHON:-python3}"
TUNA_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
TORCH_INDEX="https://download.pytorch.org/whl/cu128"
FLASH_ATTN_WHL="flash_attn-2.8.3+cu12torch2.8cxx11abiFALSE-cp312-cp312-linux_x86_64.whl"
FLASH_ATTN_URL="https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/${FLASH_ATTN_WHL}"

abspath() {
  local p="$1"
  if [[ -z "$p" ]]; then echo ""; elif [[ "$p" = /* ]]; then echo "$p"; else echo "$BLOCK_DIR/$p"; fi
}

meta_cfg() {
  "$CONFIG_PYTHON" "$BLOCK_DIR/scripts/config_value.py" "$CONFIG" meta_info "$1" --default "${2:-}"
}

SFT_UV_RAW="$(meta_cfg "environment.sft_uv")"
PYTHON_VERSION="$(meta_cfg "environment.python_version" "3.12")"
ENV_DIR="$(abspath "$SFT_UV_RAW")"
PY="$ENV_DIR/bin/python"
WHEEL_DIR="$BLOCK_DIR/artifacts/wheels"

cd "$BLOCK_DIR"

# Optional: start from a clean env to avoid previous partial installs.
# Comment these two lines if you want to reuse the existing env.
case "$ENV_DIR" in
  "$BLOCK_DIR"/artifacts/env/*) ;;
  *)
    echo "ERROR: Refusing to remove env outside $BLOCK_DIR/artifacts/env: $ENV_DIR" >&2
    echo "Set meta_info.environment.sft_uv under artifacts/env or install manually." >&2
    exit 1
    ;;
esac
rm -rf "$ENV_DIR"
uv venv "$ENV_DIR" --python "$PYTHON_VERSION"

# Data processing package with LLM scoring dependencies.
uv pip install --python "$PY" \
  --index-url "$TUNA_INDEX" \
  -e "repos/swe_data_process[llm]"

# PyTorch CUDA 12.8 wheels.
uv pip install --python "$PY" \
  torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 \
  --index-url "$TORCH_INDEX"

# LLaMA-Factory training stack.
uv pip install --python "$PY" \
  --index-url "$TUNA_INDEX" \
  -e "repos/LLaMA-Factory[torch,metrics,deepspeed,liger-kernel]" \
  --no-build-isolation

# flash-attn prebuilt wheel.
mkdir -p "$WHEEL_DIR"
if [[ ! -f "$WHEEL_DIR/$FLASH_ATTN_WHL" ]]; then
  TMP_WHL="$WHEEL_DIR/.${FLASH_ATTN_WHL}.tmp"
  rm -f "$TMP_WHL"
  wget -O "$TMP_WHL" "$FLASH_ATTN_URL"
  mv "$TMP_WHL" "$WHEEL_DIR/$FLASH_ATTN_WHL"
fi

uv pip install --python "$PY" \
  --index-url "$TUNA_INDEX" \
  "$WHEEL_DIR/$FLASH_ATTN_WHL"

# Experiment tracking.
uv pip install --python "$PY" \
  --index-url "$TUNA_INDEX" \
  wandb

"$PY" - <<'PY'
import torch
import swe_data_process
import llamafactory

print("python ok")
print("torch:", torch.__version__)
print("cuda available:", torch.cuda.is_available())
print("swe_data_process ok")
print("llamafactory ok")
PY