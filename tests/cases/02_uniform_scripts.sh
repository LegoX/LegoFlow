#!/usr/bin/env bash
# Root case 02: uniform script contract.
# Every subblock ships the four uniform scripts the block system relies on, and
# the root block itself ships them too. A missing script breaks /root:run and
# the per-block runners.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

UNIFORM=(start.sh dryrun.sh clean.sh archive_run.sh)
SUBBLOCKS=(swegen trajgen sft rl eval)
missing=()

# Root block scripts.
for s in "${UNIFORM[@]}"; do
  [[ -f "$ROOT_DIR/scripts/$s" ]] || missing+=("scripts/$s (root)")
done

# Subblock scripts.
for b in "${SUBBLOCKS[@]}"; do
  for s in "${UNIFORM[@]}"; do
    [[ -f "$ROOT_DIR/subblock/$b/scripts/$s" ]] || missing+=("subblock/$b/scripts/$s")
  done
done

if [[ ${#missing[@]} -gt 0 ]]; then
  for m in "${missing[@]}"; do echo "FAIL: missing uniform script: $m" >&2; done
  exit 1
fi
echo "PASS: uniform script contract satisfied (root + ${#SUBBLOCKS[@]} subblocks)"
