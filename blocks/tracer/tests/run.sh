#!/usr/bin/env bash
# CI test runner for the tracer block.
#
# Usage:
#   bash tests/run.sh                 # cases/ only (cheap, no LLM tokens)
#   bash tests/run.sh --with-smoke    # cases/ then smoke/ (~30 min, real LLM/Docker)
#
# Cases exit 0=PASS, 77=SKIP, anything else=FAIL.

set -uo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WITH_SMOKE="${TESTS_WITH_SMOKE:-0}"
for arg in "$@"; do
  case "$arg" in
    --with-smoke) WITH_SMOKE=1 ;;
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

sync_ci_runtime_origins() {
  [[ "${CI:-}" == "true" ]] || return 0
  local name path url abs current
  while IFS=$'\t' read -r name path url; do
    abs="$BLOCK_DIR/$path"
    # Normal CI links these paths to long-lived shared checkouts. Repository
    # transfers can leave their saved origin metadata stale even when the pin
    # and worktree are correct. Local, non-symlinked repos remain untouched.
    [[ -L "$abs" ]] || continue
    current="$(git -C "$abs" remote get-url origin 2>/dev/null || true)"
    [[ "$current" == "$url" ]] && continue
    echo "INFO: refreshing CI runtime origin for $name: $current -> $url"
    git -C "$abs" remote set-url origin "$url" || return 1
  done < <(python3 - "$BLOCK_DIR/config.yaml" <<'PY'
import sys, yaml
config = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
for name in ("harbor", "swe_data_process"):
    repo = config["meta_info"]["repositories"][name]
    print(f"{name}\t{repo['path']}\t{repo['url']}")
PY
  )
}

if ! sync_ci_runtime_origins; then
  echo "FAIL: could not refresh Tracer CI shared-runtime origins" >&2
  exit 1
fi

pass=0; fail=0; skip=0
failed_names=()

run_one() {
  local path="$1"
  local name
  name="$(basename "$path")"
  local label="$2/$name"
  echo "================================================================="
  echo ">> $label"
  echo "================================================================="
  local start_ts end_ts
  start_ts=$(date +%s)
  set +e
  bash "$path"
  local rc=$?
  set -e
  end_ts=$(date +%s)
  local dur=$((end_ts - start_ts))
  case "$rc" in
    0)  pass=$((pass+1)); echo "[PASS] $label (${dur}s)" ;;
    77) skip=$((skip+1)); echo "[SKIP] $label (${dur}s)" ;;
    *)  fail=$((fail+1)); failed_names+=("$label (rc=$rc, ${dur}s)"); echo "[FAIL] $label (rc=$rc, ${dur}s)" ;;
  esac
  echo
}

for f in "$BLOCK_DIR"/tests/cases/*.sh; do
  [[ -f "$f" ]] || continue
  run_one "$f" cases
done

if [[ "$WITH_SMOKE" == "1" ]]; then
  for f in "$BLOCK_DIR"/tests/smoke/*.sh; do
    [[ -f "$f" ]] || continue
    run_one "$f" smoke
  done
else
  echo "INFO: smoke tests skipped (re-run with --with-smoke to include them)"
fi

echo "================================================================="
echo "  tracer tests: PASS=$pass  SKIP=$skip  FAIL=$fail"
echo "================================================================="
if [[ "$fail" -gt 0 ]]; then
  for n in "${failed_names[@]}"; do echo "  FAILED: $n"; done
  exit 1
fi
exit 0
