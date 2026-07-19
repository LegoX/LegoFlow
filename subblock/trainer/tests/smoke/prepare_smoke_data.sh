#!/usr/bin/env bash
# Materialize the 512-sample SFT smoke dataset as a local LF/ShareGPT json.
#
# The production config (runtime_info.input.source) pulls the same 512 samples
# live from one exact file on the HuggingFace Hub (source.type=hf_lf with
# hf_file_name set). CI runners shouldn't depend on a live, possibly-gated HF
# pull mid-job, so we snapshot those 512 rows ONCE into a plain LF json that the
# training smoke (tests/smoke/10_train_demo.sh) consumes via source.type=local_lf.
#
# Output is a json array of {"messages": [...]} objects — exactly the
# `formatting: sharegpt, columns.messages: messages` shape train.sh registers.
# The dataset's `_score` metadata column is dropped (training ignores it).
#
# Default target is the shared CI runtime dir so `sync_runtime.sh sft` and the
# per-block "Link runtime state" CI step can stage it onto the runner alongside
# repos/ and the uv env. Override with arg 1.
#
# Usage:
#   bash tests/smoke/prepare_smoke_data.sh [OUTPUT_JSON]
#
# Re-run to refresh after the upstream HF dataset changes. Idempotent: rewrites
# OUTPUT_JSON in place.

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

# Default to this block's ignored artifacts tree. CI/runtime maintainers can
# provide SHARED_RUNTIME or an explicit output path without checking a
# machine-specific mount into the repository.
if [[ -n "${SHARED_RUNTIME:-}" ]]; then
  DEFAULT_OUT="$SHARED_RUNTIME/sft/artifacts/data/examples/lf_512.json"
else
  DEFAULT_OUT="$BLOCK_DIR/artifacts/data/examples/lf_512.json"
fi
OUT="${1:-$DEFAULT_OUT}"

HF_URL="$(cfg runtime_info.input.source.hf_hub_url)"
HF_FILE_NAME="$(cfg runtime_info.input.source.hf_file_name)"
HF_SPLIT="$(cfg runtime_info.input.source.hf_split)"
HF_SUBSET="$(cfg runtime_info.input.source.hf_subset)"   # dataset config name, if any
HF_TOKEN_VAL="${HF_TOKEN:-$(cfg runtime_info.input.credentials.hf_token)}"
[[ -n "$HF_SPLIT" ]] || HF_SPLIT="train"
[[ -n "$HF_URL" ]] || { echo "ERROR: source.hf_hub_url empty in $CONFIG" >&2; exit 1; }
if [[ -n "$HF_TOKEN_VAL" ]]; then
  export HF_TOKEN="$HF_TOKEN_VAL"
  export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN_VAL"
fi

SFT_UV="$BLOCK_DIR/$(cfg meta_info.environment.sft_uv 2>/dev/null || echo artifacts/env/lf)"
PY_BIN="$SFT_UV/bin/python"
[[ -x "$PY_BIN" ]] || PY_BIN="python3"

mkdir -p "$(dirname "$OUT")"
echo "INFO: snapshotting $HF_URL${HF_FILE_NAME:+/$HF_FILE_NAME}${HF_SUBSET:+ (subset=$HF_SUBSET)} [$HF_SPLIT] -> $OUT"

HF_URL="$HF_URL" HF_FILE_NAME="$HF_FILE_NAME" HF_SPLIT="$HF_SPLIT" HF_SUBSET="$HF_SUBSET" OUT="$OUT" "$PY_BIN" - <<'PY'
import json, os
from datasets import load_dataset
from huggingface_hub import hf_hub_download

url, split, out = os.environ["HF_URL"], os.environ["HF_SPLIT"], os.environ["OUT"]
filename = os.environ.get("HF_FILE_NAME")
if filename:
    path = hf_hub_download(repo_id=url, filename=filename, repo_type="dataset")
    ds = load_dataset("json", data_files=path, split="train")
else:
    subset = os.environ.get("HF_SUBSET") or None
    ds = load_dataset(url, name=subset, split=split)
assert "messages" in ds.column_names, f"dataset has no 'messages' column: {ds.column_names}"
rows = [{"messages": r} for r in ds["messages"]]
tmp = out + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(rows, fh, ensure_ascii=False)
os.replace(tmp, out)
print(f"INFO: wrote {len(rows)} LF records -> {out}")
PY

echo "INFO: $(python3 -c "import json,os;print(len(json.load(open(os.environ['OUT']))))" OUT="$OUT" 2>/dev/null || echo '?') records, $(du -h "$OUT" | cut -f1) on disk"
echo "OK: $OUT"
