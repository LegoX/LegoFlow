#!/usr/bin/env bash
# Root case 11: each block's own smoke config still validates once overlaid.
#
# Two smoke families exist and they are checked differently:
#
#   tests/smoke/<block>/config.yaml          root chain — case 10 validates the
#                                            SET against itself (--overlay-dir)
#   blocks/<block>/tests/smoke/config.yaml block smoke — CI copies it over the
#                                            block's config.yaml and runs
#                                            dryrun.sh, so it is validated
#                                            against the PRODUCTION siblings
#
# Nothing cheap covered the second family. A tracer block smoke whose
# `dependencies.from` still named curator.output.swe_tasks_dir — an edge
# production had since moved to merged_tasks_dir — therefore got as far as a
# 40-minute smoke job before anything noticed, and every downstream dryrun check
# reported "is empty" because tracer's dryrun blanks its variables once any
# earlier check fails.
#
# Overlay each block smoke onto a scratch copy of the tree and validate there;
# the real working tree is never touched.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail=0
checked=0
for smoke in "$ROOT_DIR"/blocks/*/tests/smoke/config.yaml; do
  [[ -f "$smoke" ]] || continue
  block="$(basename "$(dirname "$(dirname "$(dirname "$smoke")")")")"

  # A scratch tree holding every block's production config, with this one block
  # overlaid — exactly what CI does before running the block smoke.
  rm -rf "$WORK/tree"
  mkdir -p "$WORK/tree/blocks"
  cp "$ROOT_DIR/config.yaml" "$WORK/tree/config.yaml"
  for b in "$ROOT_DIR"/blocks/*/config.yaml; do
    bn="$(basename "$(dirname "$b")")"
    mkdir -p "$WORK/tree/blocks/$bn"
    cp "$b" "$WORK/tree/blocks/$bn/config.yaml"
  done
  cp "$smoke" "$WORK/tree/blocks/$block/config.yaml"

  # --block, not --root: this mirrors what the block's own dryrun.sh runs. A
  # whole-tree pass would also flag the *other* blocks' production edges that
  # legitimately do not exist in a single-block smoke (curator's smoke produces
  # no merged_tasks_dir; tracer's feeds no trainer), which is not drift.
  checked=$((checked+1))
  if out="$(python3 "$ROOT_DIR/scripts/validate_config.py" --block "$WORK/tree/blocks/$block" --schema-only 2>&1)"; then
    :
  else
    echo "FAIL: $block's smoke config does not validate when overlaid on production:"
    grep '^\[FAIL\]' <<<"$out" | sed 's/^/         /'
    fail=$((fail+1))
  fi
done

if (( fail > 0 )); then
  echo "FAIL: $fail block smoke config(s) contradict the production wiring" >&2
  exit 1
fi
echo "PASS: $checked block smoke config(s) validate when overlaid on production"
