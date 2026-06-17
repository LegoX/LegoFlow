#!/usr/bin/env bash
# CI smoke 10: sft training end-to-end at the PRODUCTION shape.
#
# Exercises the real train.sh pipeline — dataset registration (STEP 1) →
# LLaMA-Factory train YAML generation + torchrun launch on N GPUs with
# DeepSpeed ZeRO-3 (STEP 2) → runtime_info.output write (STEP 3) — on the same
# 512-sample dataset and 128K cutoff the production config trains on. The only
# things bounded for CI are wall-clock and disk:
#   * source.type=local_lf against the staged 512-sample snapshot
#     (artifacts/data/examples/lf_512.json — see tests/smoke/prepare_smoke_data.sh),
#     so the smoke never depends on a live HF pull.
#   * training.max_steps caps the run at a handful of optimizer steps
#     (the full 512 samples are still loaded + tokenized at cutoff_len=131072).
#   * save_strategy=no disables the multiplicative INTERMEDIATE checkpoint-*
#     dirs. LLaMA-Factory's do_train still writes ONE final consolidated model
#     (~16 GB) into the run dir at the end; the trap removes the whole dir on
#     exit, so nothing persists. (Disabling 16-bit gather to skip that save is
#     WORSE — DeepSpeed then dumps the full ~96 GB partitioned optimizer state.)
#
# All state is isolated from the canonical block: train.sh runs against a
# DISPOSABLE config copy (SFT_CONFIG=…) whose output_dir is a throwaway
# _smoke_* dir, so STEP 3 never touches the real config.yaml, and the trap
# removes the smoke model dir + generated YAML + temp config on exit.
#
# Pass condition: train.sh exits 0 AND the run dir has a train_results.json with
# a finite train_loss AND trainer_state.json reached >= max_steps — AND no
# intermediate checkpoint (checkpoint-* / global_step*) was written. SKIPs when
# a heavy prerequisite (uv env, base model, deepspeed config, the staged
# dataset, or >=N free GPUs) isn't present.
#
# Tunables (env): SFT_SMOKE_MAX_STEPS (default 4), SFT_SMOKE_LF_PATH (override
# the staged dataset), SFT_SMOKE_BUDGET (timeout seconds, default 2700).
#
# Heavy: real GPU training. Only invoked via `bash tests/run.sh --with-smoke`.

set -uo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"
TS="$(date +%Y%m%d-%H%M%S)"
MAX_STEPS="${SFT_SMOKE_MAX_STEPS:-4}"
BUDGET="${SFT_SMOKE_BUDGET:-2700}"

[[ -f "$CONFIG" ]] || { echo "FAIL: $CONFIG missing"; exit 1; }

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

abspath() {
  case "$1" in
    "") echo "" ;;
    /*) echo "$1" ;;
    *)  echo "$BLOCK_DIR/$1" ;;
  esac
}

# --- SKIP gates: every heavy prerequisite that may be absent on a runner ----
SFT_UV="$(abspath "$(cfg meta_info.environment.sft_uv)")"
[[ -x "$SFT_UV/bin/python" ]] || { echo "SKIP: uv env absent at $SFT_UV (install_env.sh hasn't run here)"; exit 77; }

MODEL_DIR="$(abspath "$(cfg runtime_info.input.model.model_name_or_path)")"
[[ -f "$MODEL_DIR/config.json" ]] || { echo "SKIP: base model not staged here: $MODEL_DIR"; exit 77; }

DS_CONFIG="$(abspath "$(cfg runtime_info.input.training.deepspeed)")"
[[ -f "$DS_CONFIG" ]] || { echo "SKIP: deepspeed config missing: $DS_CONFIG"; exit 77; }

N_GPUS="$(cfg runtime_info.input.infrastructure.n_gpus_per_node)"
[[ "$N_GPUS" =~ ^[0-9]+$ ]] || { echo "FAIL: n_gpus_per_node not an integer: $N_GPUS"; exit 1; }
command -v nvidia-smi >/dev/null 2>&1 || { echo "SKIP: nvidia-smi absent — no GPUs to train on"; exit 77; }

# Need N GPUs that are actually idle — a co-tenant job means we must not launch.
FREE_GPUS="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null \
  | awk '{ if ($1+0 < 2000) c++ } END { print c+0 }')"
if [[ "${FREE_GPUS:-0}" -lt "$N_GPUS" ]]; then
  echo "SKIP: only ${FREE_GPUS:-0} idle GPU(s) (< $N_GPUS) — a foreign job holds the rest; not launching training"
  exit 77
fi

# Staged dataset snapshot (local_lf). Default: block-relative path CI symlinks
# from $SHARED_RUNTIME/sft. Regenerate with tests/smoke/prepare_smoke_data.sh.
LF_PATH="${SFT_SMOKE_LF_PATH:-$BLOCK_DIR/artifacts/data/examples/lf_512.json}"
if [[ ! -s "$LF_PATH" ]]; then
  echo "SKIP: staged smoke dataset not found: $LF_PATH"
  echo "      stage it with: bash tests/smoke/prepare_smoke_data.sh"
  exit 77
fi

# --- Build a disposable config copy with the bounded overrides --------------
SMOKE_CONFIG="$BLOCK_DIR/tests/smoke/.config.smoke.$$.yaml"
SMOKE_OUTPUT_REL="_smoke_train_$TS"
SMOKE_OUTPUT_DIR="$BLOCK_DIR/artifacts/model/$SMOKE_OUTPUT_REL"
SMOKE_YAML="$BLOCK_DIR/artifacts/training_config/${SMOKE_OUTPUT_REL}.yaml"
# Throwaway dataset key so STEP 1 registers under its own name and never
# rewrites the canonical data_name's entry in the shared dataset_info.json.
SMOKE_DATA_NAME="_smoke_train_$TS"
DATASET_INFO="$BLOCK_DIR/artifacts/data/lf_data/dataset_info.json"
LOG="$BLOCK_DIR/artifacts/logs/smoke-train-$TS.log"
mkdir -p "$(dirname "$LOG")"

cleanup() {
  local rc=$?
  # train.sh's STEP 1/3 create a "<config>.lock" sidecar next to SFT_CONFIG —
  # remove it too so the smoke leaves nothing behind.
  rm -f "$SMOKE_CONFIG" "$SMOKE_CONFIG.lock" "$SMOKE_YAML"
  rm -rf "$SMOKE_OUTPUT_DIR"
  # Drop the throwaway dataset entry STEP 1 registered, under the same fcntl
  # lock train.sh uses, so dataset_info.json is left exactly as we found it.
  DATASET_INFO="$DATASET_INFO" SMOKE_DATA_NAME="$SMOKE_DATA_NAME" python3 - <<'PY' 2>/dev/null || true
import fcntl, json, os
from pathlib import Path
p = Path(os.environ["DATASET_INFO"]); key = os.environ["SMOKE_DATA_NAME"]
if p.is_file():
    lock = p.with_suffix(p.suffix + ".lock")
    with open(lock, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        info = json.loads(p.read_text(encoding="utf-8") or "{}")
        if info.pop(key, None) is not None:
            p.write_text(json.dumps(info, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
PY
  exit "$rc"
}
trap cleanup EXIT INT TERM

CONFIG="$CONFIG" SMOKE_CONFIG="$SMOKE_CONFIG" LF_PATH="$LF_PATH" \
  SMOKE_OUTPUT_REL="$SMOKE_OUTPUT_REL" SMOKE_DATA_NAME="$SMOKE_DATA_NAME" \
  MAX_STEPS="$MAX_STEPS" python3 - <<'PY' || { echo "FAIL: could not write smoke config"; exit 1; }
import os, sys
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr); sys.exit(1)
with open(os.environ["CONFIG"], encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh) or {}
ri = cfg.setdefault("runtime_info", {}).setdefault("input", {})

# Consume the staged snapshot directly (no live HF pull), keeping the
# production 512 samples + 128K cutoff intact.
src = ri.setdefault("source", {})
src["type"] = "local_lf"
src["lf_path"] = os.environ["LF_PATH"]

# Register under a throwaway key (auto-derives dataset.name) so STEP 1 never
# rewrites the canonical data_name's entry in the shared dataset_info.json.
conv = ri.setdefault("conversion", {})
conv["data_name"] = os.environ["SMOKE_DATA_NAME"]
ri.setdefault("dataset", {})["name"] = ""   # force auto-derive from data_name

tr = ri.setdefault("training", {})
tr["output_dir"] = os.environ["SMOKE_OUTPUT_REL"]
tr["max_steps"] = int(os.environ["MAX_STEPS"])
tr["save_strategy"] = "no"      # no intermediate checkpoint-* dirs
tr["warmup_ratio"] = 0.0        # meaningless over a handful of steps
tr["logging_steps"] = 1

exp = ri.setdefault("experiment", {})
exp["wandb_mode"] = "disabled"  # no wandb run dirs from a throwaway smoke

with open(os.environ["SMOKE_CONFIG"], "w", encoding="utf-8") as fh:
    yaml.safe_dump(cfg, fh, sort_keys=False, allow_unicode=True)
print(f"INFO: smoke config -> {os.environ['SMOKE_CONFIG']}")
print(f"      source=local_lf max_steps={os.environ['MAX_STEPS']} save_strategy=no output={os.environ['SMOKE_OUTPUT_REL']}")
PY

echo "INFO: smoke log     -> $LOG"
echo "INFO: dataset       -> $LF_PATH ($(python3 -c "import json,sys; print(len(json.load(open('$LF_PATH'))))" 2>/dev/null || echo '?') records)"
echo "INFO: cutoff_len    -> $(cfg runtime_info.input.training.cutoff_len)  | max_steps=$MAX_STEPS | gpus=$N_GPUS | budget=${BUDGET}s"

# --- Launch the real pipeline against the disposable config -----------------
# NOTE: no `--foreground`. train.sh launches torchrun + 8 LLaMA-Factory GPU
# workers; with `--foreground`, GNU timeout signals only the direct child
# (bash) and leaves those workers orphaned on the GPUs when the budget is hit
# (and the cleanup trap would then delete the run dir out from under live
# training). Default timeout puts the command in its own process group and
# signals the WHOLE group on expiry, so the workers die too; --kill-after
# escalates to SIGKILL for stragglers.
set +e
SFT_CONFIG="$SMOKE_CONFIG" timeout --kill-after=60s "$BUDGET" \
  bash "$BLOCK_DIR/scripts/train.sh" >>"$LOG" 2>&1
rc=$?
set -e

echo "--- last 25 log lines ---"
tail -25 "$LOG" 2>/dev/null || true
echo "-------------------------"

if [[ "$rc" == 124 ]]; then
  echo "FAIL: training exceeded ${BUDGET}s budget"
  exit 1
fi
if [[ "$rc" != 0 ]]; then
  echo "FAIL: train.sh exited rc=$rc — see $LOG"
  exit 1
fi

# --- Verify the run produced metrics and saved NO intermediate checkpoint -----
VERDICT="$(SMOKE_OUTPUT_DIR="$SMOKE_OUTPUT_DIR" MAX_STEPS="$MAX_STEPS" python3 - <<'PY'
import json, os, glob, math
out = os.environ["SMOKE_OUTPUT_DIR"]
want = int(os.environ["MAX_STEPS"])

tr_path = os.path.join(out, "train_results.json")
ts_path = os.path.join(out, "trainer_state.json")
if not os.path.isfile(tr_path):
    print("FAIL no train_results.json"); raise SystemExit
with open(tr_path) as fh:
    tr = json.load(fh)
loss = tr.get("train_loss")
if not isinstance(loss, (int, float)) or not math.isfinite(loss):
    print(f"FAIL non-finite train_loss={loss!r}"); raise SystemExit

steps = None
if os.path.isfile(ts_path):
    with open(ts_path) as fh:
        steps = json.load(fh).get("global_step")
if not isinstance(steps, int) or steps < want:
    print(f"FAIL global_step={steps} < max_steps={want}"); raise SystemExit

# save_strategy=no must suppress every INTERMEDIATE checkpoint — both HF
# `checkpoint-*` dirs and DeepSpeed `global_step*` dirs. (LLaMA-Factory's
# do_train still writes ONE final consolidated model into the run dir at the
# end; that's expected and the trap removes the whole dir on exit — nothing
# persists. We do NOT try to suppress it: disabling 16-bit gather only makes
# DeepSpeed dump the full ~96 GB partitioned optimizer state instead, which is
# far worse.)
intermediate = glob.glob(os.path.join(out, "checkpoint-*")) + glob.glob(os.path.join(out, "global_step*"))
if intermediate:
    print(f"FAIL intermediate checkpoints present despite save_strategy=no: {intermediate}"); raise SystemExit

print(f"OK loss={loss:.4f} steps={steps} no_intermediate_checkpoint")
PY
)"
echo "INFO: verify: $VERDICT"
if [[ "$VERDICT" == OK* ]]; then
  echo "PASS: sft training smoke ($VERDICT)"
  exit 0
fi
echo "FAIL: $VERDICT"
exit 1
