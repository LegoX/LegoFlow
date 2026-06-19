#!/usr/bin/env bash
# CI smoke verifier for sft.
# Pass: artifacts/model/_smoke_train_ci/train_results.json present, finite
# train_loss, trainer_state global_step >= max_steps, NO intermediate
# checkpoint-*/global_step* dirs persisted.
# Always runs cleanup (rm run dir, drop _smoke_train_ci entry from
# dataset_info.json under fcntl lock).
# Exit 0 = PASS, 77 = SKIP (no train_results.json), 1 = FAIL.

set -uo pipefail

BLOCK_DIR="${BLOCK_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUN_DIR="$BLOCK_DIR/artifacts/model/_smoke_train_ci"
DATASET_INFO="$BLOCK_DIR/artifacts/data/lf_data/dataset_info.json"
MAX_STEPS="${SFT_SMOKE_MAX_STEPS:-4}"

# Cleanup always runs, even on FAIL/SKIP.
cleanup() {
  local rc=$?
  rm -rf "$RUN_DIR" 2>/dev/null || true
  DATASET_INFO="$DATASET_INFO" python3 - <<'PY' 2>/dev/null || true
import fcntl, json, os
from pathlib import Path
p = Path(os.environ["DATASET_INFO"]); key = "_smoke_train_ci"
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
trap cleanup EXIT

if [[ ! -d "$RUN_DIR" ]]; then
  echo "SKIP: no $RUN_DIR — claude /sft:run likely SKIP'd (uv env / GPU / staged dataset missing)"
  exit 77
fi

if [[ ! -f "$RUN_DIR/train_results.json" ]]; then
  echo "FAIL: missing train_results.json in $RUN_DIR"
  exit 1
fi

VERDICT="$(RUN_DIR="$RUN_DIR" MAX_STEPS="$MAX_STEPS" python3 - <<'PY'
import json, os, glob, math
out = os.environ["RUN_DIR"]
want = int(os.environ["MAX_STEPS"])

tr_path = os.path.join(out, "train_results.json")
ts_path = os.path.join(out, "trainer_state.json")
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
