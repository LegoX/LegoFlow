#!/usr/bin/env bash
# CI test 02: repos/swegen pin + venv editable install.
# - If commit_id is non-null, assert HEAD matches.
# - Assert artifacts/envs/swegen-env exists.
# - Assert `python -c "import swegen"` succeeds inside that venv.

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

REPO_PATH="$BLOCK_DIR/repos/swegen"
COMMIT_PIN="$(cfg meta_info.repos.swegen.commit_id)"
VENV_PATH_RAW="$(cfg meta_info.environment.venv_path)"
VENV_PATH="$BLOCK_DIR/$VENV_PATH_RAW"

[[ -e "$REPO_PATH/.git" ]] || { echo "FAIL: $REPO_PATH/.git missing — run /curator:setup"; exit 1; }

if [[ -n "$COMMIT_PIN" && "$COMMIT_PIN" != "null" ]]; then
  HEAD="$(git -C "$REPO_PATH" rev-parse HEAD 2>/dev/null || true)"
  if [[ "$HEAD" != "$COMMIT_PIN" ]]; then
    echo "FAIL: repos/swegen HEAD=$HEAD does not match config commit_id=$COMMIT_PIN"
    exit 1
  fi
  echo "INFO: repos/swegen pinned at $COMMIT_PIN"
else
  echo "INFO: meta_info.repos.swegen.commit_id is null (treated as latest); skipping pin check"
fi

[[ -x "$VENV_PATH/bin/python" ]] || { echo "FAIL: venv missing at $VENV_PATH_RAW — run /curator:setup"; exit 1; }

if "$VENV_PATH/bin/python" -c "import swegen" >/dev/null 2>&1; then
  echo "PASS: repos/swegen pin + swegen importable in venv"
else
  echo "FAIL: swegen not importable in $VENV_PATH/bin/python — run pip install -e repos/swegen/"
  exit 1
fi
