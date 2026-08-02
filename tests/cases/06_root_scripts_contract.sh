#!/usr/bin/env bash
# Root case 06: root execution surfaces keep their static contracts.
# The smoke harness scripts must exist, and explicit `/root:run <block>`
# targeting must execute the selected block's `scripts/start.sh` directly.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROOT_RUN="$ROOT_DIR/.claude/plugins/root-plugin/skills/run/SKILL.md"

REQUIRED=(
  tests/run.sh
  tests/smoke/run_pipeline.sh
  tests/smoke/verify.sh
  tests/smoke/serve_checkpoint.sh
)
missing=(); nonexec=()
for r in "${REQUIRED[@]}"; do
  p="$ROOT_DIR/$r"
  if [[ ! -f "$p" ]]; then missing+=("$r"); continue; fi
  # `bash <script>` works regardless of the exec bit, but the harness invokes
  # some helpers directly; warn (not fail) if the bit is unset.
  [[ -x "$p" ]] || nonexec+=("$r")
done

if [[ ${#missing[@]} -gt 0 ]]; then
  for m in "${missing[@]}"; do echo "FAIL: missing root smoke script: $m" >&2; done
  exit 1
fi
for n in "${nonexec[@]}"; do echo "INFO: not marked executable (chmod +x recommended): $n"; done
echo "PASS: root smoke harness scripts present (${#REQUIRED[@]})"

python3 - "$ROOT_RUN" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")

targeting = text.split("### Target resolution", 1)[1].split("## Step 0", 1)[0]
if "`TARGET_DIR=./blocks/<name>/`" not in targeting:
    raise SystemExit("FAIL: /root:run does not map a selected block to TARGET_DIR")
if "Every step below operates on `TARGET_DIR`" not in targeting:
    raise SystemExit("FAIL: selected block does not use the generic direct-run path")
if "Delegate an explicitly selected block" in text:
    raise SystemExit("FAIL: selected block still uses non-operational skill delegation")

leaf = text.split("### Step 4b — Leaf block: run `scripts/start.sh`", 1)[1]
if "`cd <TARGET_DIR>` then run `bash ./scripts/start.sh`" not in leaf:
    raise SystemExit("FAIL: selected leaf does not execute TARGET_DIR/scripts/start.sh")

print("PASS: root selected-block direct execution")
PY
