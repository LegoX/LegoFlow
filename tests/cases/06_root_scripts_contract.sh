#!/usr/bin/env bash
# Root case 06: root execution surfaces keep their static contracts.
# The smoke harness scripts must exist, and explicit `/root:run <subblock>`
# targeting must delegate through the selected block's run skill.

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

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

marker = "## Step 0a — Delegate an explicitly selected subblock"
if marker not in text:
    raise SystemExit("FAIL: /root:run lacks an explicit selected-subblock delegation path")

selected = text.split(marker, 1)[1].split("## Step 1", 1)[0]
if "`/<name>:run`" not in selected:
    raise SystemExit("FAIL: selected subblock does not delegate to /<name>:run")
if "wait for it to complete" not in selected:
    raise SystemExit("FAIL: selected subblock delegation does not wait")
if "`scripts/start.sh`" not in selected or "MUST NOT" not in selected:
    raise SystemExit("FAIL: selected subblock path does not forbid direct start.sh execution")

leaf_marker = "### Step 4b — Direct leaf invocation: run `scripts/start.sh`"
if leaf_marker not in text:
    raise SystemExit("FAIL: /root:run does not isolate direct leaf execution")
leaf = text.split(leaf_marker, 1)[1].split("## Step 5", 1)[0]
if "`block_name` is unset" not in leaf or "current working directory" not in leaf:
    raise SystemExit("FAIL: start.sh execution is not limited to direct leaf invocation")

print("PASS: root selected-subblock run delegation")
PY
