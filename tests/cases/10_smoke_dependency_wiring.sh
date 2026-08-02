#!/usr/bin/env bash
# Root case 10: the smoke config SET is a self-consistent wired tree.
#
# Case 03 cross-checks the production configs; nothing cross-checked the smoke
# ones. That matters because tests/smoke/<block>/config.yaml is overlaid onto
# blocks/<block>/config.yaml before the root chain launches, so a broken
# hand-off there does not surface at overlay time — it surfaces hours later when
# a stage finds nothing to consume and the chain quietly degrades into four
# disconnected runs. Two edges were in fact declared by only one end when this
# case was written.
#
# Uses validate_config.py --overlay-dir, which resolves every block AND every
# sibling it cross-checks from the overlay set. Passing --config alone would
# check a smoke config against the PRODUCTION siblings and report the legitimate
# differences between the two sets as drift.
#
# --schema-only keeps `human` fill markers as warnings: the smoke configs get
# their endpoints injected at run time by scripts/inject_smoke_secrets.py, so an
# unfilled marker here is expected, not a defect.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VALIDATOR="$ROOT_DIR/scripts/validate_config.py"
OVERLAY="$ROOT_DIR/tests/smoke"

[[ -f "$VALIDATOR" ]] || { echo "FAIL: missing scripts/validate_config.py" >&2; exit 1; }
[[ -d "$OVERLAY" ]]   || { echo "FAIL: missing tests/smoke/" >&2; exit 1; }

OUT="$(python3 "$VALIDATOR" --root "$ROOT_DIR" --overlay-dir "$OVERLAY" --schema-only 2>&1)" && RC=0 || RC=$?

# Surface the wiring findings (OK lines are noise here; the summary carries counts).
echo "$OUT" | grep -E '^\[(FAIL|WARN)\] (dep:|output:orphan)' || true

if [[ $RC -ne 0 ]]; then
  echo "" >&2
  echo "FAIL: smoke config set has unresolved wiring:" >&2
  echo "$OUT" | grep '^\[FAIL\]' >&2
  echo "" >&2
  echo "       Each edge must be declared by BOTH ends — the consumer's" >&2
  echo "       meta_info.dependencies.from and the producer's .to. An output that" >&2
  echo "       only materialises mid-chain (e.g. trainer.output.checkpoint_path)" >&2
  echo "       must be marked 'required: false' on the consuming end." >&2
  exit 1
fi

echo "PASS: smoke config set is self-consistent ($(echo "$OUT" | tail -1))"
