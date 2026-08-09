#!/usr/bin/env bash
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"
CONFIG_PYTHON="${CONFIG_PYTHON:-python3}"
TUNA_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
TORCH_INDEX="https://download.pytorch.org/whl/cu128"
TORCH_VERSION="${TORCH_VERSION:-2.10.0}"
TORCHVISION_VERSION="${TORCHVISION_VERSION:-0.25.0}"
TORCHAUDIO_VERSION="${TORCHAUDIO_VERSION:-2.10.0}"
TRANSFORMERS_VERSION="${TRANSFORMERS_VERSION:-5.6.0}"
FLA_VERSION="${FLA_VERSION:-0.5.0}"
FSSPEC_VERSION="${FSSPEC_VERSION:-2025.3.0}"
FLASH_ATTN_VERSION="${FLASH_ATTN_VERSION:-2.8.3.post1}"
TILELANG_VERSION="${TILELANG_VERSION:-0.1.12}"
WANDB_VERSION="${WANDB_VERSION:-0.28.0}"

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

cd "$BLOCK_DIR"
command -v uv >/dev/null 2>&1 || {
  echo "ERROR: uv is required but was not found in PATH." >&2
  exit 1
}

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

# PyTorch 2.10 + CUDA 12.8 fixes multi-node training hangs.
uv pip install --python "$PY" \
  "torch==$TORCH_VERSION" \
  "torchvision==$TORCHVISION_VERSION" \
  "torchaudio==$TORCHAUDIO_VERSION" \
  --index-url "$TORCH_INDEX"

# Data processing package with LLM scoring dependencies.
uv pip install --python "$PY" \
  --index-url "$TUNA_INDEX" \
  -e "repos/swe_data_process[llm]"

# Install the patched SWE-Lego LLaMA-Factory source and its training stack.
uv pip install --python "$PY" --index-url "$TUNA_INDEX" \
  -e "repos/LLaMA-Factory"
uv pip install --python "$PY" --index-url "$TUNA_INDEX" \
  -r "repos/LLaMA-Factory/requirements/metrics.txt"
uv pip install --python "$PY" --index-url "$TUNA_INDEX" \
  -r "repos/LLaMA-Factory/requirements/deepspeed.txt"
uv pip install --python "$PY" --index-url "$TUNA_INDEX" \
  -r "repos/LLaMA-Factory/requirements/liger-kernel.txt"

# Build flash-attn against the installed PyTorch version.
uv pip install --python "$PY" \
  --index-url "$TUNA_INDEX" \
  "flash-attn==$FLASH_ATTN_VERSION" \
  --no-build-isolation

# Qwen3.5 linear-attention dependencies. These packages' direct dependencies
# were already installed with LLaMA-Factory above. Use --no-deps so this
# corrective pinning cannot replace the CUDA 12.8 PyTorch wheel with a generic
# PyPI/CUDA 13 build or upgrade transformers beyond the supported range.
uv pip install --python "$PY" \
  --index-url "$TUNA_INDEX" \
  --no-deps \
  --upgrade \
  "flash-linear-attention==$FLA_VERSION" \
  "fla-core==$FLA_VERSION" \
  "transformers==$TRANSFORMERS_VERSION" \
  "fsspec==$FSSPEC_VERSION"

# Hopper/Triton backward dependency.
uv pip install --python "$PY" \
  --index-url "$TUNA_INDEX" \
  "tilelang==$TILELANG_VERSION"

# Experiment tracking.
uv pip install --python "$PY" --index-url "$TUNA_INDEX" "wandb==$WANDB_VERSION"

# transformers <5.7 dereferences s_aux even when it is None in FA2.
# Reapply this idempotent site-packages patch after every installation.
"$PY" - <<'PY'
import os

import transformers

flash_attention = os.path.join(
    os.path.dirname(transformers.__file__), "integrations", "flash_attention.py"
)
if not os.path.exists(flash_attention):
    print(f"[install_env] transformers FA2 patch skipped: {flash_attention} not found")
    raise SystemExit(0)

with open(flash_attention, encoding="utf-8") as file:
    source = file.read()

buggy = "s_aux=s_aux.to(query.dtype),"
fixed = "s_aux=s_aux.to(query.dtype) if s_aux is not None else None,"

if fixed in source:
    print(f"[install_env] transformers {transformers.__version__} FA2 patch already applied")
elif buggy in source:
    with open(flash_attention, "w", encoding="utf-8") as file:
        file.write(source.replace(buggy, fixed, 1))
    print(f"[install_env] patched transformers FA2: {flash_attention}")
else:
    print(
        f"[install_env] transformers {transformers.__version__} FA2 target not found; "
        "the installed version may already contain the upstream fix"
    )
PY

# Separate uv installs do not automatically preserve every already-installed
# package's constraints. Fail installation instead of leaving a subtly broken
# environment.
uv pip check --python "$PY"

EXPECTED_TRANSFORMERS_VERSION="$TRANSFORMERS_VERSION" \
EXPECTED_FLA_VERSION="$FLA_VERSION" \
"$PY" - <<'PY'
import importlib
import os

import torch


def version(module):
    imported = importlib.import_module(module)
    return getattr(imported, "__version__", "ok")


print("python ok")
print("torch:", torch.__version__)
print("cuda available:", torch.cuda.is_available())
try:
    print("bundled NCCL:", torch.cuda.nccl.version())
except Exception:
    pass

modules = [
    ("transformers", "transformers"),
    ("swe_data_process", "swe_data_process"),
    ("llamafactory", "llamafactory"),
    ("flash_attn", "flash_attn"),
    ("fla (flash-linear-attention)", "fla"),
    ("liger_kernel", "liger_kernel"),
    ("tilelang", "tilelang"),
    ("deepspeed", "deepspeed"),
    ("wandb", "wandb"),
]
versions = {}
for label, module in modules:
    versions[module] = version(module)
    print(f"{label}:", versions[module])

expected_transformers = os.environ["EXPECTED_TRANSFORMERS_VERSION"]
expected_fla = os.environ["EXPECTED_FLA_VERSION"]
if versions["transformers"] != expected_transformers:
    raise RuntimeError(
        f"transformers version drift: expected {expected_transformers}, got {versions['transformers']}"
    )
if versions["fla"] != expected_fla:
    raise RuntimeError(f"FLA version drift: expected {expected_fla}, got {versions['fla']}")

# Import the parser path that performs LLaMA-Factory's dependency checks.
importlib.import_module("llamafactory.hparams")
print("LLaMA-Factory dependency check: OK")
PY
