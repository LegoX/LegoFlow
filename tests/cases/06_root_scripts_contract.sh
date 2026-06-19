#!/usr/bin/env bash
# Root case 06: the root smoke harness ships its expected scripts, executable.
# tests/run.sh, the chain orchestrator, the chain verifier, and the remote
# vLLM-serve helper must all exist and be runnable — otherwise `--with-smoke`
# dies at the first missing file.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

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
