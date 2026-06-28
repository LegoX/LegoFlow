#!/usr/bin/env bash
# CI test 02: repos/terminal-lego pin + venv dependency.
# - Assert repos/terminal-lego is checked out and HEAD matches the config pin.
# - Assert artifacts/envs/terminalgen-env exists (SKIP if absent — venv is local).
# - Assert `python -c "import requests"` succeeds inside that venv.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

cfg() {
  python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f: d = yaml.safe_load(f) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

REPO_PATH="$BLOCK_DIR/repos/terminal-lego"
COMMIT_PIN="$(cfg meta_info.repos.terminal-lego.commit_id)"
VENV_PATH_RAW="$(cfg meta_info.environment.venv_path)"
VENV_PATH="$BLOCK_DIR/$VENV_PATH_RAW"

[[ -e "$REPO_PATH/.git" ]] || { echo "FAIL: $REPO_PATH/.git missing — run: git submodule update --init repos/terminal-lego"; exit 1; }

if [[ -n "$COMMIT_PIN" && "$COMMIT_PIN" != "null" ]]; then
  HEAD="$(git -C "$REPO_PATH" rev-parse HEAD 2>/dev/null || true)"
  if [[ "$HEAD" != "$COMMIT_PIN" ]]; then
    echo "FAIL: repos/terminal-lego HEAD=$HEAD does not match config commit_id=$COMMIT_PIN"
    exit 1
  fi
  echo "INFO: repos/terminal-lego pinned at $COMMIT_PIN"
else
  echo "FAIL: meta_info.repos.terminal-lego.commit_id must be pinned (got null)"
  exit 1
fi

# Pipeline entrypoints must exist in the pinned checkout.
for f in scraper/so_scraper.py generator/task_generator.py validator/validate_tasks.py; do
  [[ -f "$REPO_PATH/$f" ]] || { echo "FAIL: $REPO_PATH/$f missing in pinned checkout"; exit 1; }
done

if [[ ! -x "$VENV_PATH/bin/python" ]]; then
  echo "SKIP: venv missing at $VENV_PATH_RAW — run /terminalgen:setup (pin + entrypoints OK)"
  exit 77
fi

if "$VENV_PATH/bin/python" -c "import requests, yaml" >/dev/null 2>&1; then
  echo "PASS: repos/terminal-lego pinned + entrypoints present + requests/PyYAML importable in venv"
else
  echo "FAIL: requests/PyYAML not importable in $VENV_PATH/bin/python — run pip install -r requirements.txt"
  exit 1
fi
