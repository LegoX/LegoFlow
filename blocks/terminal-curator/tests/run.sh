#!/usr/bin/env bash
# Run offline regressions; --with-smoke additionally builds and validates both
# hand-authored tasks in real Harbor Oracle and NOP environments.
# A requested smoke without Docker returns 77, never a passing smoke result.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WITH_SMOKE="${TESTS_WITH_SMOKE:-0}"
for arg in "$@"; do
  case "$arg" in
    --with-smoke) WITH_SMOKE=1 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "ERROR: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

TEST_PYTHON="${TERMINAL_CURATOR_TEST_PYTHON:-}"
if [[ -z "$TEST_PYTHON" ]]; then
  for candidate in "$BLOCK_DIR/artifacts/env/tests-uv/bin/python" \
                   "$BLOCK_DIR/artifacts/env/terminal-curator/bin/python" python3; do
    if "$candidate" -c 'import pytest, requests, yaml, tomli_w' >/dev/null 2>&1; then
      TEST_PYTHON="$candidate"
      break
    fi
  done
fi
if [[ -z "$TEST_PYTHON" ]]; then
  echo 'ERROR: install tests/requirements.txt in a Python 3.11+ environment, then set TERMINAL_CURATOR_TEST_PYTHON.' >&2
  exit 2
fi

echo 'Running offline regressions (no LLM, Docker, or external network calls).'
"$TEST_PYTHON" -m pytest -q "$BLOCK_DIR/tests" --ignore="$BLOCK_DIR/tests/fixtures"
if [[ "$WITH_SMOKE" != 1 ]]; then
  echo 'INFO: real Harbor smoke not requested; use --with-smoke to run it.'
  exit 0
fi
if ! command -v docker >/dev/null 2>&1; then
  echo 'SKIP: real Harbor smoke requires Docker; offline tests passed, smoke was not run.' >&2
  exit 77
fi
if ! timeout 20 docker info >/dev/null 2>&1; then
  echo 'SKIP: Docker daemon unavailable; offline tests passed, smoke was not run.' >&2
  exit 77
fi

export TERMINAL_CURATOR_SMOKE_STARTED_AT
TERMINAL_CURATOR_SMOKE_STARTED_AT="$(date -u +%s)"
bash "$BLOCK_DIR/scripts/start.sh" --config tests/smoke/config.yaml
TERMINAL_CURATOR_TEST_PYTHON="$TEST_PYTHON" bash "$BLOCK_DIR/tests/smoke/verify.sh" --config tests/smoke/config.yaml
